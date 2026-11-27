#!/usr/bin/env python3
"""Hourly orchestrator for the combined 50/50 paper portfolio (public Hyperliquid data only; no orders, no wallets, no keys).
  1. Donchian 55 sleeve: once per UTC day, after the 00:00 UTC daily close (dn_run.py).
  2. HBO sleeve: every run, catches up every missed closed hourly bar (hbo_live.py).
  3. Monthly capital rebalance between the two sleeves (virtual cash transfer, no trading cost).
  4. Combined equity log + static dashboard (site.py)."""
import os, sys, json, csv, time, subprocess, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import pandas as pd
import hl_api as API, hbo as HB, hbo_live as HL
START_DAY = '2026-11-26'          # first Donchian signal day (daily close 00:00 UTC of 27 Nov)
SLEEVE = 5000.0
HOUR = 3600000

def read_json(p): return json.load(open(p)) if os.path.exists(p) else None
def write_json(p, o): tmp = p + '.tmp'; json.dump(o, open(tmp, 'w'), indent=1); os.replace(tmp, p)

def run_donchian(dd, cache, now_ms, say):
    st = read_json(os.path.join(dd, 'state.json'))
    yday = (pd.Timestamp(now_ms, unit='ms').floor('D') - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    due = st is None or st.get('last_processed') is None or st['last_processed'] < yday
    if not due: say(f"Donchian: up to date (last daily close processed {st['last_processed']}); nothing to do."); return 0
    cmd = [sys.executable, os.path.join(HERE, 'dn_run.py'), '--state-dir', dd, '--cache-dir', cache, '--start-date', START_DAY,
           '--history-days', '500', '--capital', str(SLEEVE)]
    say('Donchian: running daily step ...')
    r = subprocess.run(cmd, capture_output=True, text=True)
    say(r.stdout[-6000:]); 
    if r.returncode: say(f'Donchian exit code {r.returncode}: {r.stderr[-1500:]}')
    return r.returncode

def dn_equity_now(dd, mids):
    st = read_json(os.path.join(dd, 'state.json'))
    if not st: return None, None
    eq = st['equity']
    for c, p in st['positions'].items():
        m = mids.get(c)
        if m: eq += p['units'] * (m - p['last_price'])
    return eq, st

def maybe_rebalance(base, mids, now_ms, say):
    mp = os.path.join(base, 'combined', 'meta.json'); meta = read_json(mp)
    dd = os.path.join(base, 'donchian'); hd = os.path.join(base, 'hbo')
    dn = read_json(os.path.join(dd, 'state.json')); hs = read_json(os.path.join(hd, 'state.json'))
    if not (dn and hs and meta): return
    now = pd.Timestamp(now_ms, unit='ms'); month = now.strftime('%Y-%m')
    if meta.get('last_rebalance_month') == month: return
    yday = (now.floor('D') - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    if dn.get('last_processed') != yday: return          # wait until the Donchian day is processed so both sleeves are fresh
    eq_d, _ = dn_equity_now(dd, mids); eq_h = HB.sleeve_equity(hs, mids)
    tot = eq_d + eq_h; t = tot / 2 - eq_d                # >0: move cash to Donchian
    if abs(t) < 0.005 * tot: meta['last_rebalance_month'] = month; write_json(mp, meta); say('Rebalance: sleeves already within 0.5% of 50/50.'); return
    dn['equity'] += t; dn['transfers'] = dn.get('transfers', 0.0) + t
    k = len(hs['subs'])
    for s in hs['subs']: s['cash'] -= t / k; s['transfers'] -= t / k
    hs['transfers'] = hs.get('transfers', 0.0) - t
    write_json(os.path.join(dd, 'state.json'), dn); write_json(os.path.join(hd, 'state.json'), hs)
    row = dict(time_utc=HB.ts_of(now_ms), moved_to='Donchian' if t > 0 else 'HBO', usd=round(abs(t), 2), donchian_before=round(eq_d, 2), hbo_before=round(eq_h, 2))
    HL.append_csv(os.path.join(base, 'combined', 'transfers.csv'), list(row), [row])
    meta['last_rebalance_month'] = month; write_json(mp, meta)
    say(f"Rebalance: moved US$ {abs(t):.2f} to {row['moved_to']} (Donchian {eq_d:.2f} / HBO {eq_h:.2f} before).")

def combined_row(base, mids, now_ms):
    dd = os.path.join(base, 'donchian'); hs = read_json(os.path.join(base, 'hbo', 'state.json'))
    eq_d, dn = dn_equity_now(dd, mids); eq_h = HB.sleeve_equity(hs, mids) if hs else None
    if eq_d is None or eq_h is None: return None
    return dict(time_utc=HB.ts_of(now_ms), equity_usd=round(eq_d + eq_h, 4), donchian_usd=round(eq_d, 4), hbo_usd=round(eq_h, 4))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=os.path.join(HERE, '..', 'data'))
    ap.add_argument('--cache-dir', default=os.path.join(HERE, '..', '.cache', 'candles_1d'))
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'index.html'))
    ap.add_argument('--no-site', action='store_true'); ap.add_argument('--skip-donchian', action='store_true')
    a = ap.parse_args()
    base = os.path.abspath(a.data_dir); now_ms = int(time.time() * 1000)
    for d in ('donchian', 'hbo', 'combined'): os.makedirs(os.path.join(base, d), exist_ok=True)
    say = lambda s: print(f"{time.strftime('%F %T', time.gmtime())} UTC | {s}", flush=True)
    mp = os.path.join(base, 'combined', 'meta.json')
    if not os.path.exists(mp):
        write_json(mp, dict(start_utc=HB.ts_of(now_ms), start_capital=2 * SLEEVE, last_rebalance_month=pd.Timestamp(now_ms, unit='ms').strftime('%Y-%m')))
    rc = 0
    if not a.skip_donchian: rc = run_donchian(os.path.join(base, 'donchian'), os.path.abspath(a.cache_dir), now_ms, say)
    r = HL.run(os.path.join(base, 'hbo'), now_ms, capital=SLEEVE, say=say)
    hrc = r if isinstance(r, int) else 0
    mids = API.fetch_mids()
    maybe_rebalance(base, mids, now_ms, say)
    row = combined_row(base, mids, now_ms)
    if row:
        p = os.path.join(base, 'combined', 'equity.csv'); rows = list(csv.DictReader(open(p))) if os.path.exists(p) else []
        if rows and rows[-1]['time_utc'][:13] == row['time_utc'][:13]: rows = rows[:-1]
        rows.append(row)
        with open(p + '.tmp', 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(row)); w.writeheader(); [w.writerow(x) for x in rows]
        os.replace(p + '.tmp', p)
        say(f"Combined equity US$ {row['equity_usd']:.2f} (Donchian {row['donchian_usd']:.2f} | HBO {row['hbo_usd']:.2f})")
    if not a.no_site:
        import site_build
        site_build.build_to(base, a.out, int(time.time() * 1000), dict(donchian_rc=rc, hbo_rc=hrc), mids)
        say(f'dashboard written to {a.out}')
    sys.exit(1 if (hrc not in (0,) and hrc != 3) else 0)

if __name__ == '__main__': main()
