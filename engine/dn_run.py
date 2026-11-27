#!/usr/bin/env python3
"""Paper portfolio: Donchian 55 + 3-ATR trailing stop on Hyperliquid perps.
Forward simulation only. Public read-only API. No orders, no wallets, no keys.
Run once a day after the 00:00 UTC daily close. Idempotent; catches up missed days.
Config: Donchian 55 / 27-day opposite exit / 3-ATR trailing stop, vol target 35%, max gross 3x, 25% per coin.

Execution model (v2, after the realism audit):
  * Signals are computed on the closed daily candle (00:00 UTC). Orders are NOT filled at that close: they are
    filled at the price observed when the script runs (allMids snapshot, real l2Book for the taker share). If a day
    is processed late (catch-up), the fill is the close of the 15-minute candle that starts at the daily close.
  * All positions are marked at that same execution snapshot, so equity, fills and P&L share one set of prices.
  * Costs: 70% maker fills (0.015% fee, filled at mid) / 30% taker (0.045% fee + real book slippage).
  * Funding: real hourly fundingHistory between two snapshots on the real hourly notional (units x hourly price).
  * Sizes rounded to szDecimals, orders below US$ 10 are skipped, initial margin uses per-coin maxLeverage.
  * Idle cash yield (4%/yr, NOT paid by Hyperliquid) is optional and always shown as its own line."""
import os,sys,json,time,argparse,fcntl,csv
import numpy as np,pandas as pd,requests
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
from engine_dn import build_X,sig_donchian,apply_stop,target_weights

API='https://api.hyperliquid.xyz/info'
CFG=dict(start_capital=5000.0,univ=40,donchian=55,stop_atr=3,port_vt=0.35,max_lev=3.0,pos_cap=0.25,
         taker_fee=0.00045,slip=0.0003,maker_fee=0.00015,fill_prob=0.7,cash_rate=0.0,band=0.1,
         min_notional=10.0,max_im_frac=0.8,live_window_h=12,catchup_delay_min=15,history_days=0)
DAY=86400000; HOUR=3600000
EXCLUDE={'APP'}   # never traded or shown (owner's rule)
TRADE_COLS=['date','coin','action','side','qty','price','notional_usd','fee_usd','realized_pnl_usd','funding_usd','position_after','reason',
            'signal_close','stop_level','slippage_usd','fill_source']
EQ_COLS=['date','exec_time_utc','equity_usd','return_since_start_pct','price_pnl_usd','funding_usd','fees_usd','slippage_usd','cash_yield_usd',
         'gross_exposure','margin_used_usd','maint_margin_usd','n_positions','n_trades','equity_ex_yield_usd']

def fee_rate():
    '''Blended commission per side: 70% maker + 30% taker (slippage is modelled separately in the fill price).'''
    return CFG['fill_prob']*CFG['maker_fee']+(1-CFG['fill_prob'])*CFG['taker_fee']
def unit_cost():
    '''All-in blended cost per side with the legacy flat-slippage model (used by the close-price replay check).'''
    return CFG['fill_prob']*CFG['maker_fee']+(1-CFG['fill_prob'])*(CFG['taker_fee']+CFG['slip'])

def post(body,tries=6):
    for k in range(tries):
        try:
            r=requests.post(API,json=body,timeout=30)
            if r.status_code==429: time.sleep(10*(k+1)); continue
            r.raise_for_status(); return r.json()
        except requests.RequestException:
            time.sleep(5*(k+1))
    raise RuntimeError(f'API failed: {body.get("type")}')

# ---------------- meta (listing, szDecimals, max leverage) ----------------
def meta_path(cdir): return os.path.join(os.path.dirname(cdir),'meta_live.json')
def fetch_meta(cdir):
    m=post({"type":"meta"})
    out={a['name']:dict(szDecimals=int(a['szDecimals']),maxLeverage=float(a['maxLeverage']),delisted=bool(a.get('isDelisted')),
                        onlyIsolated=bool(a.get('onlyIsolated')) or a.get('marginMode') in ('strictIsolated','noCross')) for a in m['universe']}
    json.dump(dict(fetched_ms=int(time.time()*1000),assets=out),open(meta_path(cdir),'w'))
    return out
def load_meta(cdir):
    f=meta_path(cdir)
    return json.load(open(f))['assets'] if os.path.exists(f) else {}

# ---------------- candles cache ----------------
def refresh_candles(cdir,meta,extra=()):
    names=sorted(({n for n,a in meta.items() if not a['delisted']}|set(extra))-EXCLUDE)
    now=int(time.time()*1000); failed=[]
    for n in names:
        f=os.path.join(cdir,f'{n}.json')
        old=json.load(open(f)) if os.path.exists(f) else []
        st=(old[-1]['t']-3*DAY) if old else (now-CFG['history_days']*DAY if CFG.get('history_days') else 1672531200000)
        try: new=post({"type":"candleSnapshot","req":{"coin":n,"interval":"1d","startTime":st,"endTime":now}})
        except RuntimeError: failed.append(n); continue
        if not isinstance(new,list): failed.append(n); continue
        m={c['t']:c for c in old}; m.update({c['t']:c for c in new})
        out=[m[k] for k in sorted(m)]
        tmp=f+'.tmp'; json.dump(out,open(tmp,'w')); os.replace(tmp,f)
        time.sleep(0.15)
    return failed

def load_panel(cdir,now_ms):
    cols={}
    for fn in os.listdir(cdir):
        if not fn.endswith('.json') or fn[:-5] in EXCLUDE: continue
        d=json.load(open(os.path.join(cdir,fn)))
        if not isinstance(d,list) or not d: continue
        df=pd.DataFrame(d)
        df=df[df['T']<now_ms]          # closed candles only
        if df.empty: continue
        df['date']=pd.to_datetime(df['t'],unit='ms')
        for k in 'ohlcv': df[k]=df[k].astype(float)
        df['qv']=df['v']*df['c']
        cols[fn[:-5]]=df.set_index('date')
    P={k:pd.DataFrame({n:cols[n][k] for n in cols}).sort_index() for k in ['c','h','l','n','qv']}
    return P

# ---------------- execution environments ----------------
_bt_fund=None
def day_funding(coin,d,source):
    """Daily sum of hourly funding rates for UTC day d (legacy daily model, used by the close-price replay)."""
    global _bt_fund
    if source=='backtest':
        if _bt_fund is None:
            sys.path.insert(0,os.path.join(HERE,'..','src')); os.chdir(os.path.join(HERE,'..'))
            from engine import load; _bt_fund=load()['fund']
        try: return float(_bt_fund.loc[d,coin])
        except KeyError: return 0.0
    st=int(d.tz_localize('UTC').timestamp()*1000) if d.tzinfo is None else int(d.timestamp()*1000)
    rows=post({"type":"fundingHistory","coin":coin,"startTime":st,"endTime":st+DAY-1})
    return float(sum(float(r['fundingRate']) for r in rows)) if rows else 0.0

class CloseEnv:
    """Legacy model: everything at the daily close (used by sanity_replay_check against the backtest engine)."""
    mode='close'; fill_source='close'; min_notional=0.0
    def __init__(self,d,C,fsource='backtest'):
        self.d=d; self.C=C; self.fsource=fsource; self.warns=[]
        self.ts=int(d.tz_localize('UTC').timestamp()*1000)+DAY; self.fee_rate=unit_cost()
    def mark(self,coin): 
        v=self.C.get(coin,np.nan); return float(v) if np.isfinite(v) else None
    def tradable(self,coin): return True
    def round_units(self,coin,u): return u
    def max_lev(self,coin): return 1e9
    def fill_price(self,coin,px,dq): return px
    def funding(self,coin,units,prev_px,t0,t1): return -units*prev_px*day_funding(coin,self.d,self.fsource)
    def accrual_years(self,t0,t1): return 1/365.0

class LiveEnv:
    """Forward model: mids/books observed when the script runs ('live'), or the 15-minute candle after the close ('hist')."""
    def __init__(self,mode,d,meta,fsource='api'):
        self.mode=mode; self.d=d; self.meta=meta; self.warns=[]; self.fsource=fsource
        T_d=int(d.tz_localize('UTC').timestamp()*1000)+DAY; self.T_d=T_d
        self.fee_rate=fee_rate(); self.min_notional=CFG['min_notional']
        self._books={}; self._hist={}; self._h1={}
        if mode=='live':
            self.ts=int(time.time()*1000); self.fill_source='live_mid_book'
            self.mids={k:float(v) for k,v in post({"type":"allMids"}).items()}
        else:
            self.ts=T_d+CFG['catchup_delay_min']*60000; self.fill_source='candle_15m_est'; self.mids={}
    def mark(self,coin):
        if self.mode=='live':
            v=self.mids.get(coin); return v if v and v>0 else None
        if coin not in self._hist:
            try:
                rows=post({"type":"candleSnapshot","req":{"coin":coin,"interval":"15m","startTime":self.T_d,"endTime":self.T_d+3*900000}})
                r=[x for x in rows if x['t']==self.T_d] if isinstance(rows,list) else []
                self._hist[coin]=float(r[0]['c']) if r else None
            except RuntimeError: self._hist[coin]=None
        return self._hist[coin]
    def tradable(self,coin):
        a=self.meta.get(coin); return bool(a) and not a['delisted'] and self.mark(coin) is not None
    def round_units(self,coin,u):
        sd=self.meta.get(coin,{}).get('szDecimals',None)
        return round(u,sd) if sd is not None else u
    def max_lev(self,coin): return self.meta.get(coin,{}).get('maxLeverage',3.0)
    def _taker_slip(self,coin,dq):
        """Fractional adverse slippage of a taker order vs the book mid (walks the real l2Book in live mode)."""
        flat=CFG['slip']
        if self.mode!='live': return flat
        if coin not in self._books:
            try: self._books[coin]=post({"type":"l2Book","coin":coin})['levels']
            except Exception: self._books[coin]=None
        lv=self._books[coin]
        if not lv or not lv[0] or not lv[1]: return flat
        bid=float(lv[0][0]['px']); ask=float(lv[1][0]['px']); mid=(bid+ask)/2
        side=lv[1] if dq>0 else lv[0]; need=abs(dq); got=0.0; cost=0.0
        for l in side:
            q=min(need-got,float(l['sz'])); got+=q; cost+=q*float(l['px'])
            if got>=need-1e-12: break
        if got<need-1e-12: cost+=(need-got)*float(side[-1]['px'])
        avg=cost/need
        return max(0.0,(avg/mid-1) if dq>0 else (1-avg/mid))
    def fill_price(self,coin,px,dq):
        s=(1-CFG['fill_prob'])*self._taker_slip(coin,dq)
        return px*(1+s) if dq>0 else px*(1-s)
    def accrual_years(self,t0,t1): return (t1-t0)/(365.0*DAY)
    def funding(self,coin,units,prev_px,t0,t1):
        """Hourly funding between two snapshots: sum over hourly rates of -units x hourly price x rate."""
        rows=post({"type":"fundingHistory","coin":coin,"startTime":int(t0)+1,"endTime":int(t1)})
        rows=[(int(r['time']),float(r['fundingRate'])) for r in rows if t0<int(r['time'])<=t1] if isinstance(rows,list) else []
        expected=int((t1-60000)//HOUR-(t0//HOUR))
        if coin not in self._h1:
            try: cs=post({"type":"candleSnapshot","req":{"coin":coin,"interval":"1h","startTime":int(t0)-2*HOUR,"endTime":int(t1)}})
            except RuntimeError: cs=[]
            self._h1[coin]={int(c['t']):float(c['o']) for c in cs} if isinstance(cs,list) else {}
        h1=self._h1[coin]
        pay=0.0
        for ts,rate in rows:
            px=h1.get(ts//HOUR*HOUR,prev_px); pay+=-units*px*rate
        if len(rows)<expected:
            miss=expected-len(rows); avg=float(np.mean([r for _,r in rows])) if rows else 0.0
            pay+=-units*prev_px*avg*miss
            self.warns.append(f'funding {coin}: {len(rows)}/{expected} hourly rows from API; {miss} hour(s) filled with the average rate')
        return pay

# ---------------- state / logs ----------------
def load_state(sd):
    f=os.path.join(sd,'state.json')
    return json.load(open(f)) if os.path.exists(f) else None
def save_state(sd,s):
    f=os.path.join(sd,'state.json'); tmp=f+'.tmp'
    json.dump(s,open(tmp,'w'),indent=1); os.replace(tmp,f)
def append_csv(path,cols,rows):
    new=not os.path.exists(path)
    with open(path,'a',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=cols)
        if new: w.writeheader()
        for r in rows: w.writerow({k:r.get(k,'') for k in cols})
def truncate_after(path,last):
    """Crash recovery: drop rows dated after the last day committed in state.json."""
    if not os.path.exists(path) or last is None: return
    df=pd.read_csv(path)
    keep=df[df['date']<=last]
    if len(keep)<len(df): keep.to_csv(path,index=False)

def unrealized(P): return sum((p['last_price']-p['entry'])*p['units'] for p in P.values())
def audit(st,tol=1e-4):
    """Accounting identity: equity = start + realized + unrealized + funding + cash yield - fees (slippage is inside realized/unrealized)."""
    L=st['ledger']; chk=st['start_capital']+st.get('transfers',0.0)+L['realized']+unrealized(st['positions'])+L['funding']+L['cash_yield']-L['fees']
    if abs(chk-st['equity'])>tol*max(1.0,st['start_capital']/10000): raise RuntimeError(f"ACCOUNTING IDENTITY BROKEN: equity {st['equity']:.6f} vs ledger {chk:.6f}")
    return chk

# ---------------- one simulated day ----------------
def process_day(st,d,C,tg,sig,sig2,elig,lv,env,stale=(),first_day_accrual=False):
    """d = signal day (daily candle closed at 00:00 UTC of d+1). Orders are filled by `env` (live snapshot / 15m candle / close)."""
    ds=d.strftime('%Y-%m-%d'); fee=env.fee_rate
    P=st['positions']; L=st.setdefault('ledger',dict(realized=0.0,funding=0.0,fees=0.0,cash_yield=0.0,slippage=0.0))
    eq=st['equity']; warns=[]
    t1=env.ts; t0=st.get('last_exec_ts'); is_start=(t0 is None)
    if is_start and first_day_accrual: t0=t1-DAY; is_start=False
    # ---- idle cash yield (optional, not paid by Hyperliquid) and funding, between the two execution snapshots
    start_gross=sum(abs(p['units']*p['last_price']) for p in P.values())/eq if eq>0 else 0.0
    cash_yield=0.0 if is_start else max(0.0,1.0-start_gross)*eq*CFG['cash_rate']*env.accrual_years(t0,t1)
    price_pnl=0.0; fund_tot=0.0
    for coin,p in P.items():
        px=env.mark(coin)
        if px is None: px=p['last_price']; warns.append(f'{coin}: no price at execution time, kept previous mark')
        price_pnl+=p['units']*(px-p['last_price'])
        fpay=0.0 if is_start else env.funding(coin,p['units'],p['last_price'],t0,t1)
        p['funding_usd']+=fpay; fund_tot+=fpay
        p['last_price']=px
    eq+=price_pnl+fund_tot+cash_yield
    L['funding']+=fund_tot; L['cash_yield']+=cash_yield
    # ---- targets: tradability filter, margin/leverage guards
    tg={k:float(v) for k,v in tg.items() if v!=0}
    for coin in list(tg):
        if not env.tradable(coin): warns.append(f'{coin}: not tradable now (delisted/no price), no new position'); tg.pop(coin)
    for coin in stale:
        if coin in P:
            tg[coin]=P[coin]['units']*P[coin]['last_price']/eq   # daily candle missing: hold, do not trade
            warns.append(f'{coin}: daily candle missing/stale, position held without trading')
    for coin in list(tg):
        if abs(tg[coin])>CFG['pos_cap']: tg[coin]=np.sign(tg[coin])*CFG['pos_cap']
    gsum=sum(abs(v) for v in tg.values())
    if gsum>CFG['max_lev']: k=CFG['max_lev']/gsum; tg={c:v*k for c,v in tg.items()}; warns.append(f'targets scaled by {k:.3f} to respect max leverage')
    im=sum(abs(v)*eq/env.max_lev(c) for c,v in tg.items())
    if im>CFG['max_im_frac']*eq: k=CFG['max_im_frac']*eq/im; tg={c:v*k for c,v in tg.items()}; warns.append(f'targets scaled by {k:.3f} to keep initial margin <= {CFG["max_im_frac"]:.0%} of equity')
    # ---- plan with no-trade band (same rule as backtest) + hard caps on drift
    coins=set(P)|set(tg); plan={}
    for coin in coins:
        px=env.mark(coin)
        if px is None: px=P[coin]['last_price'] if coin in P else None
        if px is None: continue
        w=(P[coin]['units']*px/eq) if coin in P else 0.0
        t=float(tg.get(coin,0.0))
        if coin in stale and env.tradable(coin): continue
        if coin in P and not env.tradable(coin): t=0.0; plan[coin]=(w,t,px); continue
        over_cap=abs(w)>CFG['pos_cap']+1e-9 and t!=0 and np.sign(w)==np.sign(t)
        if abs(t-w)>CFG['band']*max(abs(t),0.01) or over_cap: plan[coin]=(w,t,px)
    trades=[]; fees_tot=0.0; slip_tot=0.0
    for coin,(w,t,px) in sorted(plan.items()):
        old=P[coin]['units'] if coin in P else 0.0
        newu=env.round_units(coin,t*eq/px) if t!=0 else 0.0
        if t==0:
            if coin in P and not env.tradable(coin): reason='delisted_or_no_price'
            else: reason='stop' if sig.get(coin,0)!=0 and sig2.get(coin,0)==0 else ('universe_exit' if not elig.get(coin,False) else 'channel_exit')
        elif old==0: reason='initial_position' if st['last_processed'] is None else 'breakout_entry'
        elif np.sign(old)!=np.sign(newu): reason='breakout_entry'
        else: reason='rebalance'
        if old!=0 and (newu==0 or np.sign(newu)!=np.sign(old)): legs=[(-old,'close')]+([(newu,'open')] if newu!=0 else [])
        elif old==0: legs=[(newu,'open')]
        else: legs=[(round(newu-old,8),'increase' if abs(newu)>abs(old) else 'reduce')]
        stop_lvl=P[coin].get('stop') if coin in P else None
        for dq,act in legs:
            if dq==0: continue
            if act!='close' and abs(dq)*px<env.min_notional:
                warns.append(f'{coin}: {act} of US$ {abs(dq)*px:.2f} below the US$ {env.min_notional:.0f} minimum order, skipped'); continue
            p=P.get(coin)
            pf=env.fill_price(coin,px,dq) if (env.tradable(coin) or env.mode=='close') else px
            f=abs(dq)*pf*fee; slip=abs(dq)*abs(pf-px); real=0.0; fund_closed=0.0
            if act=='open':
                P[coin]=dict(units=dq,entry=pf,entry_date=ds,last_price=px,funding_usd=0.0,fees_usd=f,realized_usd=0.0)
            elif act=='increase':
                p['entry']=(p['entry']*abs(p['units'])+pf*abs(dq))/(abs(p['units'])+abs(dq)); p['units']+=dq; p['fees_usd']+=f
            else:
                q=abs(dq); real=(pf-p['entry'])*q*np.sign(p['units'])
                p['realized_usd']+=real; p['fees_usd']+=f; L['realized']+=real
                if act=='close': fund_closed=p['funding_usd']; P.pop(coin)
                else: p['units']+=dq
            eq-=f+slip; fees_tot+=f; slip_tot+=slip; L['fees']+=f; L['slippage']+=slip
            after=P[coin]['units'] if coin in P else 0.0
            side=('long' if dq>0 else 'short') if act in('open','increase') else ('long' if dq<0 else 'short')
            trades.append(dict(date=ds,coin=coin,action=act,side=side,qty=round(abs(dq),8),price=pf,
                notional_usd=round(abs(dq)*pf,2),fee_usd=round(f,6),realized_pnl_usd=round(real,6),
                funding_usd=round(fund_closed,6),position_after=round(after,8),
                reason=reason if act!='close' or reason not in('breakout_entry','initial_position') else 'flip',
                signal_close=C.get(coin,''),stop_level=(round(stop_lvl,8) if (stop_lvl and reason=='stop') else ''),
                slippage_usd=round(slip,6),fill_source=env.fill_source))
    for coin,p in P.items():
        v=lv.get(coin,np.nan); p['stop']=None if not np.isfinite(v) else float(v)
    st['equity']=eq; st['last_processed']=ds; st['last_exec_ts']=int(t1)
    # ---- post-trade risk metrics
    gross_usd=sum(abs(p['units']*p['last_price']) for p in P.values()); gross=gross_usd/eq
    im=sum(abs(p['units']*p['last_price'])/env.max_lev(c) for c,p in P.items()); mm=im/2
    if gross>CFG['max_lev']+1e-9: warns.append(f'gross exposure {gross:.3f}x above the {CFG["max_lev"]}x cap (drift)')
    if im>eq: warns.append('initial margin above equity')
    for c,p in P.items():
        if abs(p['units']*p['last_price'])/eq>CFG['pos_cap']*(1+CFG['band'])+1e-9: warns.append(f'{c}: above the per-coin cap after rebalance')
    audit(st)
    warns+=env.warns
    eqrow=dict(date=ds,exec_time_utc=pd.Timestamp(t1,unit='ms').strftime('%Y-%m-%d %H:%M:%S'),equity_usd=round(eq,4),
        return_since_start_pct=round((eq/(st['start_capital']+st.get('transfers',0.0))-1)*100,4),price_pnl_usd=round(price_pnl,4),funding_usd=round(fund_tot,4),
        fees_usd=round(fees_tot,4),slippage_usd=round(slip_tot,4),cash_yield_usd=round(cash_yield,4),gross_exposure=round(gross,4),
        margin_used_usd=round(im,2),maint_margin_usd=round(mm,2),n_positions=len(P),n_trades=len(trades),
        equity_ex_yield_usd=round(eq-L['cash_yield'],4))
    st['last_day']=dict(eq=eqrow,trades=trades,warnings=warns)
    return trades,eqrow

def summary(st):
    e=st['last_day']['eq']; tr=st['last_day']['trades']; L=st['ledger']
    L_=[f"Signal day (daily close, UTC): {e['date']} | executed {e['exec_time_utc']} UTC",
       f"Equity: US$ {e['equity_usd']:.2f} ({e['return_since_start_pct']:+.2f}% since start {st['start_date']}, start US$ {st['start_capital']:.0f}); without idle-cash yield: US$ {e['equity_ex_yield_usd']:.2f}",
       f"Period: price P&L US$ {e['price_pnl_usd']:+.2f} | funding US$ {e['funding_usd']:+.2f} (negative = paid) | fees US$ {-e['fees_usd']:+.2f} | slippage US$ {-e.get('slippage_usd',0):+.2f} | idle cash yield US$ {e.get('cash_yield_usd',0):+.2f} (hypothetical) | gross {e['gross_exposure']:.2f}x | initial margin US$ {e.get('margin_used_usd',0):.0f}",
       f"Open positions ({len(st['positions'])}):"]
    if st['positions']:
        L_.append(f"  {'coin':<10}{'side':<6}{'entry':>12}{'price':>12}{'notional_usd':>14}{'unrealized_usd':>16}{'stop':>12}")
        for c,p in sorted(st['positions'].items(),key=lambda kv:-abs(kv[1]['units']*kv[1]['last_price'])):
            u=(p['last_price']-p['entry'])*p['units']
            stp=f"{p['stop']:.6g}" if p.get('stop') else '-'
            L_.append(f"  {c:<10}{'long' if p['units']>0 else 'short':<6}{p['entry']:>12.6g}{p['last_price']:>12.6g}{p['units']*p['last_price']:>14.2f}{u:>16.2f}{stp:>12}")
    else: L_.append('  (none)')
    op=[t for t in tr if t['action']=='open']; cl=[t for t in tr if t['action']=='close']; ot=[t for t in tr if t['action'] in('increase','reduce')]
    L_.append(f"Trades opened today ({len(op)}): "+(', '.join(f"{t['coin']} {t['side']} US$ {t['notional_usd']:.0f} @ {t['price']:.6g}" for t in op) or 'none'))
    L_.append(f"Trades closed today ({len(cl)}): "+(', '.join(f"{t['coin']} {t['side']} @ {t['price']:.6g} realized US$ {t['realized_pnl_usd']:+.2f} [{t['reason']}]" for t in cl) or 'none'))
    if ot: L_.append(f"Resizes today ({len(ot)}): "+', '.join(f"{t['coin']} {t['action']} US$ {t['notional_usd']:.0f}" for t in ot))
    L_.append(f"Ledger: realized {L['realized']:+.4f} + unrealized {unrealized(st['positions']):+.4f} + funding {L['funding']:+.4f} + idle-cash yield {L['cash_yield']:+.4f} - fees {L['fees']:.4f} = equity change {st['equity']-st['start_capital']:+.4f} (identity OK)")
    for w in st['last_day'].get('warnings',[]): L_.append('WARNING: '+w)
    return '\n'.join(L_)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--state-dir',default=HERE)
    ap.add_argument('--cache-dir',default=os.path.join(HERE,'cache','candles_1d'))
    ap.add_argument('--capital',type=float,default=None)
    ap.add_argument('--history-days',type=int,default=0)
    ap.add_argument('--start-date',default='2026-11-26')
    ap.add_argument('--dry-run',action='store_true',help='compute and print, write nothing (no state/log changes)')
    ap.add_argument('--offline',action='store_true',help='skip candle refresh, use cache')
    ap.add_argument('--until',default=None,help='process days up to this date (replay/testing)')
    ap.add_argument('--funding-source',default='api',choices=['api','backtest'])
    ap.add_argument('--price-mode',default='auto',choices=['auto','close'],help="auto: live snapshot for the latest day, 15m candle for older days; close: legacy fills at the daily close")
    ap.add_argument('--dump-weights',default=None)
    a=ap.parse_args()
    sd=a.state_dir; os.makedirs(sd,exist_ok=True)
    lock=open(os.path.join(sd,'.lock'),'w')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError: print('Another run is in progress; exiting.'); return
    cdir=a.cache_dir; os.makedirs(cdir,exist_ok=True)
    if a.capital: CFG['start_capital']=a.capital
    CFG['history_days']=a.history_days
    st=load_state(sd)
    held=list(st['positions']) if st else []
    failed=[]
    if not a.offline:
        meta=fetch_meta(cdir); failed=refresh_candles(cdir,meta,held)
    else: meta=load_meta(cdir)
    now=int(time.time()*1000)
    P=load_panel(cdir,now)
    X=build_X(P['c'],P['h'],P['l'],P['n'],P['qv'],CFG['univ'])
    s=sig_donchian(X,CFG['donchian']); s2,lv=apply_stop(X,s,CFG['stop_atr'],levels=True)
    tgt=target_weights(X,s2,port_vt=CFG['port_vt'],max_lev=CFG['max_lev'],pos_cap=CFG['pos_cap'])
    days=tgt.index
    if a.until: days=days[days<=pd.Timestamp(a.until)]
    if not len(days): print('No closed daily candles available.'); return
    last_closed=days[-1].strftime('%Y-%m-%d')
    expected_last=(pd.Timestamp(now,unit='ms').floor('D')-pd.Timedelta(days=1))
    if not a.until and days[-1]<expected_last:
        print(f"STALE DATA: last closed daily candle is {last_closed} but {expected_last.strftime('%Y-%m-%d')} should be available. Nothing written; re-run later.")
        sys.exit(3)
    if st is None:
        if pd.Timestamp(a.start_date) not in days:
            print(f"Start pending: the {a.start_date} daily candle is not closed yet (closes 00:00 UTC next day = 21:00 BRT). Last closed day: {last_closed}. Nothing written.")
            return
        st=dict(start_date=a.start_date,start_capital=CFG['start_capital'],equity=CFG['start_capital'],
                positions={},last_processed=None,last_exec_ts=None,config=CFG,created_utc=pd.Timestamp.now('UTC').isoformat(),
                ledger=dict(realized=0.0,funding=0.0,fees=0.0,cash_yield=0.0,slippage=0.0))
    if not a.dry_run:
        truncate_after(os.path.join(sd,'trades.csv'),st['last_processed'])
        truncate_after(os.path.join(sd,'equity.csv'),st['last_processed'])
    todo=[d for d in days if d>=pd.Timestamp(st['start_date']) and (st['last_processed'] is None or d>pd.Timestamp(st['last_processed']))]
    W=[]; elig_df=X['elig']; C_df=X['c']; allw=[]
    for i,d in enumerate(todo):
        Cd=C_df.loc[d]; C=Cd.dropna().to_dict(); tg=tgt.loc[d]; tg=tg[tg!=0].to_dict()
        T_d=int(d.tz_localize('UTC').timestamp()*1000)+DAY
        live=(a.price_mode=='auto' and i==len(todo)-1 and 0<=now-T_d<CFG['live_window_h']*HOUR)
        if a.price_mode=='close': env=CloseEnv(d,C,a.funding_source)
        else:
            try: env=LiveEnv('live' if live else 'hist',d,meta,a.funding_source)
            except RuntimeError: env=LiveEnv('hist',d,meta,a.funding_source); env.warns.append('live prices unavailable: used 15m candle estimate')
        if env.mode!='close' and not meta: print('No meta (listing/szDecimals) available; run online once.'); sys.exit(4)
        coins_now=set(st['positions'])|set(tg)
        stale={c for c in coins_now if c in Cd.index and not np.isfinite(Cd[c])} | {c for c in st['positions'] if c not in Cd.index}
        stale|={c for c in failed if c in coins_now}
        trades,eqrow=process_day(st,d,C,tg,s.loc[d].to_dict(),s2.loc[d].to_dict(),elig_df.loc[d].to_dict(),lv.loc[d].to_dict(),env,stale=stale)
        if a.dump_weights: W.append({'date':d,**{c:p['units']*p['last_price']/st['equity'] for c,p in st['positions'].items()}})
        if not a.dry_run:
            append_csv(os.path.join(sd,'trades.csv'),TRADE_COLS,trades)
            append_csv(os.path.join(sd,'equity.csv'),EQ_COLS,[eqrow])
            save_state(sd,st)
    if a.dump_weights: pd.DataFrame(W).set_index('date').to_csv(a.dump_weights)
    hdr='[DRY RUN - nothing written] ' if a.dry_run else ''
    print(f"{hdr}Processed {len(todo)} new day(s); last closed candle {last_closed}.")
    if st.get('last_day'): print(summary(st))
    else: print('No day processed yet.')

if __name__=='__main__': main()
