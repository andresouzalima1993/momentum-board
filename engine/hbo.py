"""HBO sleeve: 4-day hourly breakout with a volume spike, 9 equal virtual sub-accounts (n in 48/72/96 x time exit 72/96/120 h).
Paper simulation only. Reads public Hyperliquid data. Never sends orders, never uses wallets or keys.

Core (pure, no network): process_bar() replays one closed hourly bar exactly like the research backtest
(scheduled exits at the open, stop-loss on the bar's range, entries, hourly funding, mark at the close,
daily kill switch, signals at the close -> orders for the next open).
Live layer: execute_pending_live() fills the orders that the last closed bar generated at the price observed
when the script runs (allMids + real l2Book), instead of the candle open."""
import math, json, os, time
HOUR = 3600000
CFG = dict(start_capital=5000.0, ns=(48, 72, 96), holds=(72, 96, 120), volx=5.0, stop_atr=6.0, dtrend=20,
           risk=0.005, max_pos=8, gross=3.0, pos_cap=0.75, dloss=0.03, taker=0.00045, min_notional=10.0,
           max_im_frac=0.8, top=20, live_rules=True)
EXCLUDE = {'APP'}

def isn(x): return x is None or (isinstance(x, float) and math.isnan(x))
def slip_from_rank(r):
    r = 40 if isn(r) else r
    return 0.0003 if r <= 5 else 0.0004 if r <= 15 else 0.0006
def day_of(b): return time.strftime('%Y-%m-%d', time.gmtime(b * 3600))
def ts_of(ms): return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ms / 1000))

def new_sub(n, hold, cap):
    return dict(n=n, hold=hold, start=cap, transfers=0.0, cash=cap, realized=0.0, fees=0.0, funding=0.0, slippage=0.0,
                pos={}, pend=[], pend_exit={}, day=None, day_eq=cap, killed=False, eq_close=cap)
def new_state(cap_total, cfg=CFG, start_ms=None):
    subs = [new_sub(n, h, cap_total / (len(cfg['ns']) * len(cfg['holds']))) for n in cfg['ns'] for h in cfg['holds']]
    return dict(start_capital=cap_total, start_ms=start_ms, last_bar=None, subs=subs, transfers=0.0, created_utc=ts_of(time.time() * 1000))

def unreal(sub, px=None):
    u = 0.0
    for c, p in sub['pos'].items():
        x = (px or {}).get(c)
        if isn(x) or x is None: x = p['last']
        u += p['q'] * p['sd'] * (x - p['ep'])
    return u
def sub_equity(sub, px=None): return sub['cash'] + unreal(sub, px)
def sleeve_equity(S, px=None): return sum(sub_equity(s, px) for s in S['subs'])

def audit(S, tol=1e-6):
    """Accounting identity per sub-account: equity = start + transfers + realized + unrealized + funding - fees (slippage is inside realized/unrealized)."""
    for s in S['subs']:
        chk = s['start'] + s['transfers'] + s['realized'] + s['funding'] - s['fees']
        if abs(chk - s['cash']) > tol * max(1.0, s['start'] / 10000): raise RuntimeError(f"HBO ACCOUNTING IDENTITY BROKEN n{s['n']}/h{s['hold']}: cash {s['cash']:.6f} vs ledger {chk:.6f}")
    return True

# ---------------- closing / opening primitives ----------------
def _close(sub, coin, price, ref, fee_rate, why, when_ms, log, source, cfg):
    p = sub['pos'].pop(coin)
    pnl = p['q'] * p['sd'] * (price - p['ep']); fee = abs(p['q']) * price * fee_rate
    slip = abs(p['q']) * abs(price - ref)
    sub['cash'] += pnl - fee; sub['realized'] += pnl; sub['fees'] += fee; sub['slippage'] += slip
    log.append(dict(time_utc=ts_of(when_ms), sub=f"n{sub['n']}/h{sub['hold']}", coin=coin, action='close', side='long' if p['sd'] > 0 else 'short',
                    qty=round(p['q'], 8), price=price, notional_usd=round(p['q'] * price, 2), fee_usd=round(fee, 6), realized_pnl_usd=round(pnl, 6),
                    funding_usd=round(p['fund'], 6), reason=why, stop_level=round(p['stop'], 8) if why == 'stop_loss' else '',
                    slippage_usd=round(slip, 6), fill_source=source, entry_price=p['ep'], entry_time=p['t_in']))
    return pnl - fee

def _open(sub, coin, sd, q, fill, ref, stopd, exit_i, when_ms, log, source, fee_rate, sdist, R):
    fee = q * fill * fee_rate; slip = q * abs(fill - ref)
    sub['cash'] -= fee; sub['fees'] += fee; sub['slippage'] += slip
    sub['pos'][coin] = dict(q=q, sd=sd, ep=fill, stop=fill - sd * stopd, exit_i=exit_i, last=fill, fee_in=fee, fund=0.0, R=R,
                            t_in=ts_of(when_ms), sdist=stopd)
    log.append(dict(time_utc=ts_of(when_ms), sub=f"n{sub['n']}/h{sub['hold']}", coin=coin, action='open', side='long' if sd > 0 else 'short',
                    qty=round(q, 8), price=fill, notional_usd=round(q * fill, 2), fee_usd=round(fee, 6), realized_pnl_usd=0.0, funding_usd=0.0,
                    reason='breakout_entry', stop_level=round(fill - sd * stopd, 8), slippage_usd=round(slip, 6), fill_source=source,
                    entry_price='', entry_time=''))

def _size_entry(sub, od, fill, eqnow, gross_now, cfg, meta, im_other):
    """Backtest sizing: risk % of equity at the stop, capped by 75% of equity and by the 3x gross room; live rules add szDecimals, min notional, margin."""
    q = cfg['risk'] * eqnow / od['stopd']
    room = cfg['gross'] * eqnow - gross_now
    notional = min(q * fill, cfg['pos_cap'] * eqnow, room)
    if notional <= cfg['min_notional']: return None
    if cfg.get('live_rules'):
        a = (meta or {}).get(od['coin'])
        if a is None or a.get('delisted'): return None
        maxlev = a.get('maxLeverage', 3.0)
        room_im = cfg['max_im_frac'] * eqnow - im_other
        if room_im <= 0: return None
        notional = min(notional, room_im * maxlev)
        q = notional / fill
        sd_ = a.get('szDecimals')
        if sd_ is not None:
            f = 10 ** sd_; q = math.floor(q * f + 1e-9) / f
        if q <= 0 or q * fill < cfg['min_notional']: return None
        return q
    return notional / fill

# ---------------- one closed hourly bar (same order of operations as the backtest) ----------------
def process_bar(S, b, bar, sigs, elig, rank, fund_rate, log, cfg=CFG, meta=None, source='candle_open'):
    """b = hour index of the bar start. bar = dict(O,H,L,C: coin->float). sigs[k] = {coin: (side, stop_distance)} at the close of this bar.
    elig = set of coins in the top-N universe for this bar's UTC day. rank(coin)->liquidity rank. fund_rate(coin)->rate paid for this bar."""
    O, H, L, C = bar['O'], bar['H'], bar['L'], bar['C']
    d = day_of(b); t_open = b * HOUR; t_close = (b + 1) * HOUR; tk = cfg['taker']
    for k, sub in enumerate(S['subs']):
        if sub['day'] != d: sub['day'] = d; sub['day_eq'] = sub['eq_close']; sub['killed'] = False
        pos = sub['pos']
        # 1) scheduled market exits at the open
        for coin in list(sub['pend_exit']):
            why = sub['pend_exit'][coin]
            if coin in pos:
                o = O.get(coin)
                if isn(o): continue
                sl = slip_from_rank(rank(coin)); p = pos[coin]
                _close(sub, coin, o - p['sd'] * o * sl, o, tk, why, t_open, log, source, cfg)
        sub['pend_exit'] = {}
        # 2) stop-loss on the bar's range
        for coin in list(pos):
            p = pos[coin]; o, h, l = O.get(coin), H.get(coin), L.get(coin)
            if isn(o): continue
            st = p['stop']; sl = slip_from_rank(rank(coin))
            if p['sd'] > 0:
                if o <= st: _close(sub, coin, o * (1 - sl), o, tk, 'stop_loss', t_close, log, 'candle_gap', cfg); continue
                if l <= st: _close(sub, coin, st * (1 - sl), st, tk, 'stop_loss', t_close, log, 'candle_stop', cfg); continue
            else:
                if o >= st: _close(sub, coin, o * (1 + sl), o, tk, 'stop_loss', t_close, log, 'candle_gap', cfg); continue
                if h >= st: _close(sub, coin, st * (1 + sl), st, tk, 'stop_loss', t_close, log, 'candle_stop', cfg); continue
        # 3) pending entries at the open (only reached for catch-up bars; live orders were filled at run time)
        newpend = []
        eqnow = sub['cash'] + sum(p['q'] * p['sd'] * ((O.get(c) if not isn(O.get(c)) else p['last']) - p['ep']) for c, p in pos.items())
        if sub['pend'] and not sub['killed']:
            for od in sub['pend']:
                coin = od['coin']
                if od['exp'] < b or coin in pos: continue
                o, h, l = O.get(coin), H.get(coin), L.get(coin)
                if isn(o): newpend.append(od); continue
                sd = od['sd']; sl = slip_from_rank(rank(coin)); fill = o * (1 + sd * sl)
                if len(pos) >= cfg['max_pos']: continue
                gross_now = sum(abs(p['q']) * (O.get(c) if not isn(O.get(c)) else p['last']) for c, p in pos.items())
                im_other = _sleeve_im(S, meta, exclude=None) if cfg.get('live_rules') else 0.0
                q = _size_entry(sub, od, fill, eqnow, gross_now, cfg, meta, im_other)
                if q is None: continue
                _open(sub, coin, sd, q, fill, o, od['stopd'], od['exit_i'], t_open, log, source, tk, od['stopd'], cfg['risk'] * eqnow)
                stop = pos[coin]['stop']; c_ = C.get(coin)
                if (sd > 0 and l <= stop) or (sd < 0 and h >= stop):      # same-bar adverse check (conservative: stop before anything favourable)
                    px = stop * (1 - sd * sl); _close(sub, coin, px, stop, tk, 'stop_loss', t_close, log, 'candle_stop', cfg)
        sub['pend'] = newpend if not sub['killed'] else []
        # 4) hourly funding and mark at the bar close
        for coin, p in pos.items():
            x = C.get(coin)
            if isn(x): continue
            f = fund_rate(coin) * p['q'] * x * p['sd']
            sub['cash'] -= f; sub['funding'] -= f; p['fund'] -= f; p['last'] = x
        eq = sub['cash'] + sum(p['q'] * p['sd'] * (p['last'] - p['ep']) for p in pos.values())
        sub['eq_close'] = eq
        # 5) daily kill switch
        if cfg['dloss'] and not sub['killed'] and eq < sub['day_eq'] * (1 - cfg['dloss']):
            sub['killed'] = True; sub['pend'] = []
            for coin in pos: pos[coin]['why'] = 'kill_switch'; sub['pend_exit'][coin] = 'kill_switch'
        # 6) time exits
        for coin, p in pos.items():
            if b >= p['exit_i']: sub['pend_exit'].setdefault(coin, p.get('why', 'time_exit'))
        # 7) new signals -> orders for the next open
        if sub['killed']: continue
        sg = sigs[k]
        nz = [c for c, (sd, stopd) in sg.items() if c in elig and c not in pos and stopd > 0]
        nz.sort(key=lambda c: 99 if isn(rank(c)) else rank(c))
        have = {od['coin'] for od in sub['pend']}
        for c in nz:
            if c in have: continue
            sd, stopd = sg[c]
            sub['pend'].append(dict(coin=c, sd=int(sd), stopd=float(stopd), exp=b + 1, exit_i=b + sub['hold']))
    S['last_bar'] = b

def _sleeve_im(S, meta, exclude=None):
    im = 0.0
    for s in S['subs']:
        for c, p in s['pos'].items():
            ml = ((meta or {}).get(c) or {}).get('maxLeverage', 3.0)
            im += abs(p['q']) * p['last'] / ml
    return im

# ---------------- live execution of the orders generated by the last closed bar ----------------
def execute_pending_live(S, mids, slip_book, rank, log, now_ms, cfg=CFG, meta=None):
    """Fill pending exits then entries at the observed mid. Slippage charged = larger of the real l2Book slippage and a conservative
    assumption (3/4/6 bp by liquidity rank): paper-size orders are tiny relative to live books, so the book alone would flatter the result."""
    tk = cfg['taker']
    def slip(coin, notional): return max(slip_from_rank(rank(coin)), slip_book(coin, notional))
    for sub in S['subs']:
        pos = sub['pos']
        for coin, why in list(sub['pend_exit'].items()):
            m = mids.get(coin)
            if coin in pos and m:
                p = pos[coin]; sl = slip(coin, p['q'] * m)
                _close(sub, coin, m * (1 - p['sd'] * sl), m, tk, why, now_ms, log, 'live_mid_book', cfg)
        sub['pend_exit'] = {}
        eqnow = sub_equity(sub, mids)
        if sub['pend'] and not sub['killed']:
            for od in sub['pend']:
                coin = od['coin']; m = mids.get(coin)
                if coin in pos or not m or coin in EXCLUDE: continue
                if len(pos) >= cfg['max_pos']: continue
                sd = od['sd']; sl0 = slip_from_rank(rank(coin)); fill0 = m * (1 + sd * sl0)
                gross_now = sum(abs(p['q']) * (mids.get(c) or p['last']) for c, p in pos.items())
                im_other = _sleeve_im(S, meta)
                q = _size_entry(sub, od, fill0, eqnow, gross_now, cfg, meta, im_other)
                if q is None: continue
                sl = slip(coin, q * m); fill = m * (1 + sd * sl)
                _open(sub, coin, sd, q, fill, m, od['stopd'], od['exit_i'], now_ms, log, 'live_mid_book', tk, od['stopd'], cfg['risk'] * eqnow)
        sub['pend'] = []
    for sub in S['subs']:
        for c, p in sub['pos'].items():
            if mids.get(c): p['last'] = float(mids[c])
