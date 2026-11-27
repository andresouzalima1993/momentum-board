"""Live runner for the HBO sleeve (public Hyperliquid data only). Idempotent; catches up missed hours."""
import os, sys, json, csv, time, math
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hbo as HB, hbo_signals as HS, hl_api as API
from hbo import HOUR, EXCLUDE, ts_of, day_of

TRADE_COLS = ['time_utc', 'sub', 'coin', 'action', 'side', 'qty', 'price', 'notional_usd', 'fee_usd', 'realized_pnl_usd', 'funding_usd', 'reason',
              'stop_level', 'slippage_usd', 'fill_source', 'entry_price', 'entry_time']
EQ_COLS = ['time_utc', 'equity_usd', 'realized_usd', 'unrealized_usd', 'funding_usd', 'fees_usd', 'slippage_usd', 'n_positions', 'gross_exposure', 'margin_used_usd', 'subs_killed']

def append_csv(path, cols, rows):
    new = not os.path.exists(path)
    with open(path, 'a', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        if new: w.writeheader()
        for r in rows: w.writerow({k: r.get(k, '') for k in cols})
def load_state(sd):
    f = os.path.join(sd, 'state.json'); return json.load(open(f)) if os.path.exists(f) else None
def save_state(sd, S):
    f = os.path.join(sd, 'state.json'); tmp = f + '.tmp'; json.dump(S, open(tmp, 'w'), indent=1); os.replace(tmp, f)

def universe_for_days(days, cache, meta, excl=EXCLUDE, top=20):
    """Top-N perps by trailing 30-day average dollar volume, known at the previous daily close (point in time). Cached per day.
    One daily-candle download serves every requested day."""
    need = [d for d in days if d not in cache]
    if not need: return {d: cache[d] for d in days}
    names = sorted(n for n, a in meta.items() if not a['delisted'] and n not in excl)
    try:   # pre-filter: top 60 by 24h volume plus every coin that was in a recent universe (top-20 by 30d volume is always inside this set)
        dv = API.day_volumes(); keep = set(sorted(dv, key=dv.get, reverse=True)[:60])
        for u in cache.values(): keep |= set(u)
        names = [n for n in names if n in keep]
    except Exception: pass
    first = pd.Timestamp(min(need)); last = pd.Timestamp(max(need)); start = int((first - pd.Timedelta(days=45)).timestamp() * 1000); end = int(last.timestamp() * 1000)
    qv = {}
    for n in names:
        rows = API.candles(n, '1d', start, end)
        qv[n] = pd.Series({pd.Timestamp(int(r['t']), unit='ms'): float(r['v']) * float(r['c']) for r in rows if int(r['t']) + 86400000 <= end})
        time.sleep(0.05)
    for d in need:
        d1 = pd.Timestamp(d); adv = {}
        for n, s in qv.items():
            x = s[s.index < d1]; x = x[x > 0].iloc[-30:]
            if len(x) >= 20: adv[n] = x.mean()
        rk = pd.Series(adv).rank(ascending=False)
        cache[d] = {c: int(r) for c, r in rk.items() if r <= top}
    return {d: cache[d] for d in days}

def build_panel(coins, first_bar, K, now_ms):
    start = (first_bar - 640) * HOUR
    cols = {k: {} for k in 'ohlcv'}
    for c in coins:
        rows = [r for r in API.candles(c, '1h', start, now_ms) if int(r['t']) + HOUR <= now_ms]
        if not rows: continue
        df = pd.DataFrame(rows); df['t'] = pd.to_datetime(df['t'], unit='ms'); df = df.set_index('t')
        for k in 'ohlc': cols[k][c] = df[k].astype(float)
        cols['v'][c] = df['v'].astype(float) * df['c'].astype(float)
        time.sleep(0.05)
    idx = pd.date_range(pd.Timestamp(start, unit='ms'), pd.Timestamp(K * HOUR, unit="ms"), freq='h')
    return {k: pd.DataFrame(cols[k]).reindex(idx) for k in 'ohlcv'}

class Funding:
    def __init__(self, t0_ms, now_ms): self.t0 = t0_ms; self.now = now_ms; self.d = {}; self.warn = 0
    def rate(self, coin, b):
        """Hourly rate paid at the end of bar b (payment stamped at the next hour boundary)."""
        if coin not in self.d:
            try: rows = API.funding_history(coin, self.t0, self.now)
            except Exception: rows = []
            self.d[coin] = {int(r['time']) // HOUR: float(r['fundingRate']) for r in rows}
        v = self.d[coin].get(b + 1)
        if v is None:
            self.warn += 1; known = list(self.d[coin].values())
            v = float(np.mean(known)) if known else 1.25e-5
        return v

def sleeve_row(S, mids, meta, now_ms):
    subs = S['subs']
    eq = HB.sleeve_equity(S, mids)
    gross = sum(abs(p['q']) * (mids.get(c) or p['last']) for s in subs for c, p in s['pos'].items())
    im = sum(abs(p['q']) * (mids.get(c) or p['last']) / (meta.get(c, {}).get('maxLeverage', 3.0)) for s in subs for c, p in s['pos'].items())
    return dict(time_utc=ts_of(now_ms), equity_usd=round(eq, 4), realized_usd=round(sum(s['realized'] for s in subs), 4),
                unrealized_usd=round(sum(HB.unreal(s, mids) for s in subs), 4), funding_usd=round(sum(s['funding'] for s in subs), 4),
                fees_usd=round(sum(s['fees'] for s in subs), 4), slippage_usd=round(sum(s['slippage'] for s in subs), 4),
                n_positions=sum(len(s['pos']) for s in subs), gross_exposure=round(gross / eq, 4) if eq > 0 else 0,
                margin_used_usd=round(im, 2), subs_killed=sum(1 for s in subs if s['killed']))

def run(state_dir, now_ms=None, capital=5000.0, dry=False, say=print):
    os.makedirs(state_dir, exist_ok=True)
    now_ms = now_ms or int(time.time() * 1000)
    S = load_state(state_dir); meta = API.fetch_meta()
    K = now_ms // HOUR - 1                       # last closed hourly bar (index of its start hour)
    created = False
    if S is None:
        S = HB.new_state(capital, start_ms=now_ms); S['last_bar'] = K - 1; created = True
    L = S['last_bar']
    ucache = S.setdefault('univ', {})
    todo = list(range(L + 1, K + 1))
    log = []; warns = []
    cur_day = day_of(K + 1)
    if todo:
        days = sorted({day_of(b) for b in todo} | {cur_day})
        univ = universe_for_days(days, ucache, meta)
        coins = set().union(*[set(u) for u in univ.values()])
        for s in S['subs']: coins |= set(s['pos']) | {od['coin'] for od in s['pend']}
        coins -= EXCLUDE
        P = build_panel(sorted(coins), todo[0], K, now_ms)
        ref = 'BTC' if 'BTC' in P['c'] else next(iter(P['c']))
        if np.isnan(P['c'].iloc[-1].get(ref, np.nan)):
            say(f'STALE DATA: hourly candle of bar {ts_of(K*HOUR)} not available yet. Nothing written.'); return 3
        sig = {n: HS.hbo_side({k: P[k] for k in 'ohlcv'}, n=n, volx=HB.CFG['volx'], stop_atr=HB.CFG['stop_atr'], dtrend=HB.CFG['dtrend']) for n in HB.CFG['ns']}
        fund = Funding((todo[0]) * HOUR - HOUR, now_ms)
        idx = P['c'].index
        for b in todo:
            t = pd.Timestamp(b * HOUR, unit="ms")
            if t not in idx: continue
            row = lambda k: {c: float(P[k].at[t, c]) for c in P['c'].columns}
            bar = dict(O=row('o'), H=row('h'), L=row('l'), C=row('c'))
            u = univ[day_of(b)]
            sigs = []
            for sub in S['subs']:
                sd, st = sig[sub['n']]
                r1 = sd.loc[t]; r2 = st.loc[t]
                sigs.append({c: (float(r1[c]), float(r2[c])) for c in r1.index if r1[c] != 0 and not np.isnan(r2[c])})
            rank = lambda c, u=u: u.get(c, float('nan'))
            HB.process_bar(S, b, bar, sigs, set(u), rank, lambda c, b=b: fund.rate(c, b), log, HB.CFG, meta)
        if fund.warn: warns.append(f'funding: {fund.warn} hourly rate(s) missing from the API, filled with the coin average')
    # ---- live execution of the orders created by the last closed bar
    mids = API.fetch_mids(); books = API.Books()
    u_now = universe_for_days([cur_day], ucache, meta)[cur_day]
    rank_now = lambda c: u_now.get(c, float('nan'))
    HB.execute_pending_live(S, mids, lambda c, n: books.slip(c, n), rank_now, log, now_ms, HB.CFG, meta)
    HB.audit(S)
    for d in list(ucache):
        if d < day_of(K - 24 * 5): ucache.pop(d)
    S['last_run_ms'] = now_ms
    row = sleeve_row(S, mids, meta, now_ms)
    say(f"HBO: bars processed {len(todo)} (last closed {ts_of(K*HOUR)} UTC) | fills {len(log)} | equity US$ {row['equity_usd']:.2f} | open positions {row['n_positions']} | warnings {len(warns)}")
    for w in warns: say('WARNING: ' + w)
    if not dry:
        append_csv(os.path.join(state_dir, 'trades.csv'), TRADE_COLS, log)
        eqp = os.path.join(state_dir, 'equity.csv')
        append_csv(eqp, EQ_COLS, [row]); save_state(state_dir, S)
    S['_log'] = log; S['_row'] = row; S['_warns'] = warns
    return S
