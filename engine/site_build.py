#!/usr/bin/env python3
"""Static dashboard for the combined 50/50 paper portfolio. Reads only the paper data files (and backtest_ref.json). Read-only."""
import os, json, csv, html, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); BRT = ZoneInfo('America/Sao_Paulo')
e = html.escape; HOUR = 3600000

def usd(x, sign=False, dec=2):
    if x is None: return '—'
    s = f"{abs(x):,.{dec}f}"
    return (('+' if x > 0 else '−' if x < 0 else '') if sign else ('−' if x < 0 else '')) + 'US$ ' + s
def pct(x, sign=True, dec=2):
    if x is None: return '—'
    s = f"{abs(x):.{dec}f}%"
    return (('+' if x > 0 else '−' if x < 0 else '') if sign else ('−' if x < 0 else '')) + s
def px(x):
    if x is None or x == '': return '—'
    x = float(x); a = abs(x)
    return f"{x:,.2f}" if a >= 1000 else f"{x:.4f}" if a >= 1 else f"{x:.5f}" if a >= 0.01 else f"{x:.4g}"
def cls(x): return 'pos' if (x or 0) > 0.005 else 'neg' if (x or 0) < -0.005 else ''
def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def rjson(p): return json.load(open(p)) if os.path.exists(p) else None
def rcsv(p):
    if not os.path.exists(p): return []
    with open(p, newline='') as fh: return list(csv.DictReader(fh))
def to_brt(s):
    try: return datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).astimezone(BRT)
    except ValueError: return None
def brt_str(s):
    d = to_brt(s); return d.strftime('%d %b %H:%M') if d else s

# ---------- charts ----------
def svg_lines(series, w=1000, h=250, baseline=None, vline=None, ydec=0):
    """series: list of (name, color, [(label, value)]). Shared x positions by index of the longest series."""
    allv = [v for _, _, pts in series for _, v in pts]
    if not allv: return ''
    lo, hi = min(allv), max(allv)
    if baseline is not None: lo, hi = min(lo, baseline), max(hi, baseline)
    if hi - lo < 1e-9: hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.08; lo -= pad; hi += pad
    L, R, T, B = 64, 12, 12, 26; n = max(len(p) for _, _, p in series)
    X = lambda i, m: L + (w - L - R) * (i / (m - 1) if m > 1 else 0.5)
    Y = lambda v: T + (h - T - B) * (1 - (v - lo) / (hi - lo))
    o = [f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4; y = Y(v)
        o.append(f'<line x1="{L}" x2="{w-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e5e8ef"/><text x="{L-6}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#6b7289">{v:,.{ydec}f}</text>')
    if baseline is not None:
        y = Y(baseline); o.append(f'<line x1="{L}" x2="{w-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#9aa3b5" stroke-dasharray="4 4"/>')
    if vline:
        pts = series[0][2]; idx = next((i for i, (lab, _) in enumerate(pts) if lab >= vline[0]), None)
        if idx is not None:
            x = X(idx, len(pts)); o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{h-B}" stroke="#5a4fbf" stroke-dasharray="3 3"/><text x="{x+5:.1f}" y="{T+12}" font-size="11" fill="#5a4fbf">{e(vline[1])}</text>')
    for name, color, pts in series:
        m = len(pts)
        if m == 1: o.append(f'<circle cx="{X(0,1):.1f}" cy="{Y(pts[0][1]):.1f}" r="4" fill="{color}"/>'); continue
        s = ' '.join(f'{X(i,m):.1f},{Y(v):.1f}' for i, (_, v) in enumerate(pts))
        o.append(f'<polyline points="{s}" fill="none" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke"/>')
    p0 = series[0][2]; m = len(p0)
    for i in sorted(set([0, m // 2, m - 1])) if m > 2 else range(m):
        a = 'start' if i == 0 else 'end' if i == m - 1 else 'middle'
        o.append(f'<text x="{X(i,m):.1f}" y="{h-8}" text-anchor="{a}" font-size="11" fill="#6b7289">{e(p0[i][0])}</text>')
    o.append('</svg>')
    leg = ''.join(f'<span><i style="background:{c}"></i>{e(n)}</span>' for n, c, _ in series if len(series) > 1)
    return ''.join(o) + (f'<div class="legend">{leg}</div>' if leg else '')
def downsample(rows, key, n=320, lab=lambda r: r['time_utc'][5:16]):
    pts = [(lab(r), f(r[key])) for r in rows]
    if len(pts) <= n: return pts
    step = len(pts) / n; out = [pts[int(i * step)] for i in range(n)]; out[-1] = pts[-1]; return out
def max_dd(vals):
    pk = -1e18; m = 0.0
    for v in vals: pk = max(pk, v); m = min(m, v / pk - 1)
    return m

def kpi(k, v, d='', tone=''): return f'<div class="kpi {tone}"><div class="k">{e(k)}</div><div class="v">{v}</div><div class="d">{d}</div></div>'
def table(head, rows, empty='None yet'):
    h = ''.join(f'<th{" class=l" if i == 0 else ""}>{e(x)}</th>' for i, x in enumerate(head))
    b = ''.join(rows) if rows else f'<tr class="empty-row"><td colspan="{len(head)}">{e(empty)}</td></tr>'
    return f'<div class="table-wrap"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'
def td(v, c='', l=False): return f'<td class="{("l " if l else "")}{c}">{v}</td>'

def daily_rows(base, START_CAP):
    """Daily-close snapshots (00:00 BRT = 03:00 UTC) persisted by the runner, turned into P&L / NAV rows. Sleeve P&L excludes internal transfers."""
    snaps = rcsv(os.path.join(base, 'combined', 'daily.csv')); out = []
    prev_eq = START_CAP; prev_d = prev_h = START_CAP / 2
    for r in snaps:
        eq = f(r['equity_usd']); d = f(r['donchian_usd']) - f(r['donchian_transfers']); h = f(r['hbo_usd']) - f(r['hbo_transfers'])
        out.append(dict(date=r['date'], eq=eq, pnl=eq - prev_eq, dret=(eq / prev_eq - 1) * 100, nav=eq / START_CAP, cum=(eq / START_CAP - 1) * 100,
                        dpnl=d - prev_d, hpnl=h - prev_h, intraday=False, snap=r.get('snapshot_time_utc', '')))
        prev_eq, prev_d, prev_h = eq, d, h
    return out, prev_eq, prev_d, prev_h

def daily_section(base, ceq, tot, last, START_CAP, xfers, now):
    rows, prev_eq, prev_d, prev_h = daily_rows(base, START_CAP)
    today = now.astimezone(BRT).strftime('%Y-%m-%d')
    if last:
        xd = sum((1 if x['moved_to'] == 'Donchian' else -1) * f(x['usd']) for x in xfers)
        d = f(last['donchian_usd']) - xd; h = f(last['hbo_usd']) + xd
        rows.append(dict(date=today, eq=tot, pnl=tot - prev_eq, dret=(tot / prev_eq - 1) * 100, nav=tot / START_CAP, cum=(tot / START_CAP - 1) * 100,
                         dpnl=d - prev_d, hpnl=h - prev_h, intraday=True, snap=''))
    body = []
    for r in reversed(rows):
        lab = f"{r['date']} <span class=\"badge badge-blue\">intraday</span>" if r['intraday'] else r['date']
        body.append('<tr>' + td(lab, l=True) + td(usd(r['eq'])) + td(usd(r['pnl'], True), cls(r['pnl'])) + td(pct(r['dret']), cls(r['dret']))
                    + td(f"<b>{r['nav']:.4f}</b>") + td(pct(r['cum']), cls(r['cum'])) + td(usd(r['dpnl'], True), cls(r['dpnl'])) + td(usd(r['hpnl'], True), cls(r['hpnl'])) + '</tr>')
    o = ['<section class="panel"><h2>Daily P&amp;L and NAV per unit</h2><p class="section-sub">One row per São Paulo day (BRT). Each day closes at 00:00 BRT (= 03:00 UTC), taken from the hourly equity of the 03:02 UTC run, and is labelled with the BRT date that just ended. The first row also includes the first minutes after inception (26 Nov, 23:24 BRT). '
         'NAV per unit starts at 1.0000 and equals equity ÷ US$ 10,000.00 of initial units; there are no deposits or withdrawals, and the monthly rebalance between sleeves is an internal transfer, so it never changes NAV. '
         'Sleeve P&amp;L also excludes that transfer. The current day is partial (intraday) and is measured against the last daily close.</p>']
    pts = [(r['date'][5:] + (' *' if r['intraday'] else ''), r['nav']) for r in rows]
    if len(pts) > 1: o.append(svg_lines([('NAV per unit', '#0f1f4b', pts)], h=190, baseline=1.0, ydec=4))
    o.append(table(['Date (BRT)', 'Equity', 'Daily P&L', 'Daily return', 'NAV per unit', 'Cumulative return', 'Donchian P&L', 'HBO P&L'], body, 'Waiting for the first run.'))
    o.append('</section>')
    return ''.join(o)

def build(base, now_ms, info=None, mids=None, page='live'):
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc); now_brt = now.astimezone(BRT); info = info or {}; mids = mids or {}
    meta = rjson(os.path.join(base, 'combined', 'meta.json')) or {}
    dn = rjson(os.path.join(base, 'donchian', 'state.json')); hs = rjson(os.path.join(base, 'hbo', 'state.json'))
    ceq = rcsv(os.path.join(base, 'combined', 'equity.csv')); xfers = rcsv(os.path.join(base, 'combined', 'transfers.csv'))
    dtr = rcsv(os.path.join(base, 'donchian', 'trades.csv')); deq = {r['date']: r for r in rcsv(os.path.join(base, 'donchian', 'equity.csv'))}
    htr = rcsv(os.path.join(base, 'hbo', 'trades.csv'))
    ref = rjson(os.path.join(HERE, 'backtest_ref.json')) or {}
    START_CAP = float(meta.get('start_capital', 10000.0))
    css = open(os.path.join(HERE, 'style_base.css')).read()
    extra = """
.status-line { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 12px 16px; margin: 0 0 16px; font-size: 13.5px; color: var(--navy); display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.kpi-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 0 0 16px; }
@media (min-width: 760px) { .kpi-row { grid-template-columns: repeat(3, 1fr); } }
@media (min-width: 1080px) { .kpi-row { grid-template-columns: repeat(6, 1fr); } }
.rules { margin: 6px 0 0; padding-left: 18px; font-size: 13.5px; line-height: 1.6; } .rules li { margin-bottom: 4px; } .rules b { color: var(--navy); }
.empty-row td { color: var(--muted); text-align: left; font-style: italic; }
svg.chart { height: 250px; } .panel h3 { font-size: 13px; letter-spacing: .06em; text-transform: uppercase; color: var(--navy); margin: 16px 0 4px; }
.tabs-nav { display:flex; gap:6px; padding: 0 24px; margin: 12px auto 0; max-width: 1180px; } .tabs-nav a { padding: 8px 18px; border: 1px solid var(--line-strong); border-radius: 999px; background: var(--surface); color: var(--navy); font-size: 13px; font-weight: 600; text-decoration: none; letter-spacing: .02em; } .tabs-nav a.active { background: var(--navy); color: #fff; border-color: var(--navy); }
.legend { display:flex; gap:16px; font-size:12px; color: var(--muted); margin-top:6px; } .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }
"""
    # ---- numbers ----
    last = ceq[-1] if ceq else None
    tot = f(last['equity_usd']) if last else START_CAP
    ret = (tot / START_CAP - 1) * 100
    led = dict(realized=0.0, unreal=0.0, funding=0.0, fees=0.0, slip=0.0, yld=0.0)
    d_unreal = sum((mids.get(c) or p['last_price']) * p['units'] - p['entry'] * p['units'] for c, p in (dn or {}).get('positions', {}).items())
    if dn:
        L = dn['ledger']; led['realized'] += L['realized']; led['funding'] += L['funding']; led['fees'] += L['fees']; led['slip'] += L.get('slippage', 0); led['yld'] += L.get('cash_yield', 0)
    h_unreal = 0.0
    if hs:
        for s in hs['subs']:
            led['realized'] += s['realized']; led['funding'] += s['funding']; led['fees'] += s['fees']; led['slip'] += s['slippage']
            h_unreal += sum(p['q'] * p['sd'] * ((mids.get(c) or p['last']) - p['ep']) for c, p in s['pos'].items())
    led['unreal'] = d_unreal + h_unreal
    gross = 0.0; npos = 0
    if dn: gross += sum(abs(p['units']) * (mids.get(c) or p['last_price']) for c, p in dn['positions'].items()); npos += len(dn['positions'])
    if hs: gross += sum(p['q'] * (mids.get(c) or p['last']) for s in hs['subs'] for c, p in s['pos'].items()); npos += sum(len(s['pos']) for s in hs['subs'])
    lev = gross / tot if tot > 0 else 0
    mdd = max_dd([f(r['equity_usd']) for r in ceq] + [START_CAP]) * 100 if ceq else 0.0
    # ---- status ----
    last_run = to_brt(last['time_utc']) if last else None
    age_h = (now - datetime.strptime(last['time_utc'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)).total_seconds() / 3600 if last else 99
    if not last: badge = ('badge-blue', 'Pending start')
    elif age_h <= 3: badge = ('badge-green', 'Running')
    else: badge = ('badge-red', 'Delayed')
    dlp = (dn or {}).get('last_processed', '—')
    hb = (hs or {}).get('last_bar'); hb_s = time.strftime('%d %b %H:00', time.gmtime((hb + 1) * 3600)) if hb is not None else '—'
    status = (f'Hourly runner (GitHub Actions) · last run {last_run.strftime("%d %b %Y, %H:%M") if last_run else "—"} BRT · HBO: last closed hourly bar processed up to {hb_s} UTC · '
              f'Donchian: last daily close processed {dlp} (00:00 UTC = 21:00 BRT). Missed hours and days are caught up one by one on the next run, so no trade is lost.')
    H = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
         '<meta name="robots" content="noindex,nofollow"><title>Momentum paper portfolio · ' + ('Backtest' if page == 'backtest' else 'Meme Desk' if page == 'meme' else '50/50 combo') + '</title>'
         '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">'
         f'<style>{css}{extra}</style></head><body>',
         '<header class="top"><div class="crumb">Paper trading <span class="sep">/</span> <b>Momentum board</b></div><div class="top-right">'
         '<span class="badge badge-gray">Read-only</span><span class="badge badge-gray">Paper</span>'
         f'<span class="badge {badge[0]}">{badge[1]}</span><span class="clock">Updated {now_brt.strftime("%d %b %Y, %H:%M")} BRT</span></div></header>'
         '<nav class="tabs-nav"><a href="index.html"' + (' class="active"' if page == 'live' else '') + '>Live portfolio</a>'
         '<a href="meme.html"' + (' class="active"' if page == 'meme' else '') + '>Meme Desk</a>'
         '<a href="backtest.html"' + (' class="active"' if page == 'backtest' else '') + '>Backtest</a></nav><main class="shell">']
    H.append('<section class="intro"><h2>50/50 combo: daily trend + hourly breakout</h2><p>Two strategies share one simulated Hyperliquid account of US$ 10,000.00: '
             '<b>Donchian 55</b> (daily trend following, US$ 5,000.00 sleeve) and the <b>HBO ensemble</b> (hourly volume-spike breakout, US$ 5,000.00 sleeve). '
             'Capital is rebalanced back to 50/50 once a month. Everything is simulated from public prices: no orders are sent and no wallet or key is used.</p></section>')
    H.append(f'<div class="status-line"><span class="badge {badge[0]}">{badge[1]}</span><span>{e(status)}</span></div>')
    H.append('<div class="kpi-row">' + kpi('Equity', usd(tot), f'Start {usd(START_CAP)}') + kpi('Return since start', pct(ret), '', 'up' if ret > 0 else 'down' if ret < 0 else '')
             + kpi('Realized P&L', usd(led['realized'], True), 'Closed trades, before fees', 'up' if led['realized'] > 0 else 'down' if led['realized'] < 0 else '')
             + kpi('Unrealized P&L', usd(led['unreal'], True), 'Open positions at live mid', 'up' if led['unreal'] > 0 else 'down' if led['unreal'] < 0 else '')
             + kpi('Max drawdown', pct(mdd, sign=False), 'Peak-to-trough since start (hourly)') + kpi('Leverage', f'{lev:.2f}×', f'Gross notional {usd(gross)} · {npos} open positions') + '</div>')
    # ---- equity chart ----
    H.append('<section class="panel"><h2>Equity</h2><p class="section-sub">Total simulated account value (both sleeves, positions marked at the live mid price), one point per hourly run. The dashed line is the starting capital.</p>')
    H.append(svg_lines([('Total equity', '#0f1f4b', downsample(ceq, 'equity_usd'))], baseline=START_CAP) if ceq else '<p class="note">Waiting for the first run.</p>')
    if len(ceq) > 1:
        H.append('<h3>Sleeves</h3><p class="section-sub">Each sleeve is a separate ledger. Donchian moves once a day; HBO moves every hour.</p>')
        H.append(svg_lines([('Donchian 55 sleeve', '#1b7a78', downsample(ceq, 'donchian_usd')), ('HBO ensemble sleeve', '#5a4fbf', downsample(ceq, 'hbo_usd'))]))
    H.append('</section>')
    H.append(daily_section(base, ceq, tot, last, START_CAP, xfers, now))
    # ---- ledger ----
    dn_eq = f(last['donchian_usd']) if last else 5000.0
    hb_eq = f(last['hbo_usd']) if last else 5000.0
    def lrow(name, st_cap, xf, real, unr, fund, fees, slip, eq):
        return ('<tr>' + td(name, l=True) + td(usd(st_cap)) + td(usd(xf, True)) + td(usd(real, True), cls(real)) + td(usd(unr, True), cls(unr)) + td(usd(fund, True), cls(fund))
                + td(usd(-fees, True), cls(-fees)) + td(usd(-slip, True), cls(-slip)) + td(f'<b>{usd(eq)}</b>') + '</tr>')
    rows = []
    if dn:
        L = dn['ledger']; rows.append(lrow('Donchian 55', dn['start_capital'], dn.get('transfers', 0.0), L['realized'], d_unreal, L['funding'], L['fees'], L.get('slippage', 0), dn_eq))
    if hs:
        rows.append(lrow('HBO ensemble', hs['start_capital'], hs.get('transfers', 0.0), sum(s['realized'] for s in hs['subs']), h_unreal, sum(s['funding'] for s in hs['subs']),
                         sum(s['fees'] for s in hs['subs']), sum(s['slippage'] for s in hs['subs']), hb_eq))
    rows.append('<tr>' + td('<b>Total</b>', l=True) + td(f'<b>{usd(START_CAP)}</b>') + td('') + td(f'<b>{usd(led["realized"], True)}</b>', cls(led['realized'])) + td(f'<b>{usd(led["unreal"], True)}</b>', cls(led['unreal']))
                + td(f'<b>{usd(led["funding"], True)}</b>', cls(led['funding'])) + td(f'<b>{usd(-led["fees"], True)}</b>', cls(-led['fees'])) + td(f'<b>{usd(-led["slip"], True)}</b>', cls(-led['slip'])) + td(f'<b>{usd(tot)}</b>') + '</tr>')
    H.append('<section class="panel"><h2>Ledger</h2><p class="section-sub">Equity = start + transfers + Realized P&amp;L + Unrealized P&amp;L + funding − fees. '
             'Slippage is already inside the fill prices (shown for information, not subtracted twice). Funding is the real hourly Hyperliquid funding (negative = paid). '
             'The identity is verified on every run and the run stops if it breaks. No idle-cash yield is credited (Hyperliquid pays none).</p>')
    H.append(table(['Sleeve', 'Start', 'Transfers', 'Realized P&L', 'Unrealized P&L', 'Funding', 'Fees', 'Slippage', 'Equity'], rows))
    if xfers: H.append('<p class="rule-note">Monthly rebalance transfers: ' + '; '.join(f"{brt_str(x['time_utc'])} BRT moved {usd(f(x['usd']))} to {x['moved_to']}" for x in xfers[-6:]) + '.</p>')
    H.append('</section>')
    # ---- positions: Donchian ----
    H.append('<section class="panel"><h2>Open positions · Donchian 55</h2><p class="section-sub">Daily trend sleeve. <b>Long</b> when the daily close breaks the 55-day high, <b>Short</b> when it breaks the 55-day low. '
             'Notional = position size in US$ at the live mid. The <b>Stop-loss</b> is a 3-ATR trailing level checked at each daily close.</p>')
    rows = []; deq_now = max(dn_eq, 1e-9)
    for c, p in sorted((dn or {}).get('positions', {}).items(), key=lambda kv: -abs(kv[1]['units']) * (mids.get(kv[0]) or kv[1]['last_price'])):
        m = mids.get(c) or p['last_price']; u = p['units'] * (m - p['entry']); nt = abs(p['units']) * m
        rows.append('<tr>' + td(c, l=True) + td('Long' if p['units'] > 0 else 'Short') + td(px(p['entry'])) + td(px(m)) + td(usd(nt)) + td(pct(nt / deq_now * 100, sign=False, dec=1))
                    + td(usd(u, True), cls(u)) + td(usd(p['funding_usd'], True), cls(p['funding_usd'])) + td(px(p.get('stop'))) + td(p['entry_date']) + '</tr>')
    H.append(table(['Coin', 'Side', 'Entry', 'Mark', 'Notional', '% of sleeve', 'Unrealized P&L', 'Funding', 'Stop-loss', 'Entry date'], rows, 'No open positions.'))
    H.append('</section>')
    # ---- positions: HBO ----
    agg = {}
    for s in (hs or {}).get('subs', []):
        for c, p in s['pos'].items():
            k = (c, 'Long' if p['sd'] > 0 else 'Short'); a = agg.setdefault(k, dict(q=0, cost=0, n=0, stops=[], exits=[], t0=p['t_in']))
            a['q'] += p['q']; a['cost'] += p['q'] * p['ep']; a['n'] += 1; a['stops'].append(p['stop']); a['exits'].append(p['exit_i']); a['t0'] = min(a['t0'], p['t_in'])
    H.append('<section class="panel"><h2>Open positions · HBO ensemble</h2><p class="section-sub">Hourly sleeve made of 9 equal virtual sub-accounts (breakout windows 48/72/96 h × time exits 72/96/120 h). '
             '<b>Long</b> when an hour closes above the highest high of the last n hours on at least 5× normal volume with the daily close above its 20-day average (Short mirrors it). '
             'Each trade risks 0.5% of its sub-account at a fixed <b>Stop-loss</b> 6 hourly ATRs away and exits by time. Positions are grouped by coin and side.</p>')
    rows = []
    for (c, sd), a in sorted(agg.items(), key=lambda kv: -kv[1]['q'] * (mids.get(kv[0][0]) or 0)):
        m = mids.get(c) or 0; avg = a['cost'] / a['q']; sg = 1 if sd == 'Long' else -1; u = a['q'] * sg * (m - avg); nt = a['q'] * m
        stop = max(a['stops']) if sg > 0 else min(a['stops']); xt = time.strftime('%d %b %H:00', time.gmtime(min(a['exits']) * 3600 + 3600))
        xb = to_brt(xt and time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(min(a['exits']) * 3600 + 3600)))
        rows.append('<tr>' + td(c, l=True) + td(sd) + td(str(a['n']) + ' of 9') + td(px(avg)) + td(px(m)) + td(usd(nt)) + td(usd(u, True), cls(u)) + td(px(stop)) + td(brt_str(a['t0']) + ' BRT') + td(xb.strftime('%d %b %H:00') + ' BRT' if xb else '—') + '</tr>')
    H.append(table(['Coin', 'Side', 'Versions', 'Avg entry', 'Mark', 'Notional', 'Unrealized P&L', 'Nearest stop-loss', 'First entry', 'Earliest time exit'], rows, 'No open positions.'))
    if hs:
        vr = []
        for s in hs['subs']:
            eq = s['cash'] + sum(p['q'] * p['sd'] * ((mids.get(c) or p['last']) - p['ep']) for c, p in s['pos'].items())
            vr.append('<tr>' + td(f"Breakout {s['n']} h · exit {s['hold']} h", l=True) + td(usd(eq)) + td(pct((eq / (s['start'] + s['transfers']) - 1) * 100), cls(eq - s['start'])) + td(str(len(s['pos'])))
                      + td('Flat until next UTC day' if s['killed'] else 'Active') + '</tr>')
        H.append('<h3>Sub-accounts</h3><p class="section-sub">Each version has its own cash, positions and daily 3% loss limit (kill switch: close everything and stop entering until the next UTC day).</p>')
        H.append(table(['Version', 'Equity', 'Return', 'Open positions', 'Status'], vr))
    H.append('</section>')
    # ---- trades ----
    T = []
    for r in dtr:
        d = deq.get(r['date'], {}); T.append(dict(t=(d.get('exec_time_utc') or r['date'] + ' 00:00:00'), sleeve='Donchian', coin=r['coin'], act=r['action'], side=r['side'], px=r['price'],
                                                  nt=r['notional_usd'], real=r['realized_pnl_usd'], why=r['reason'], slip=r.get('slippage_usd', 0), src=r.get('fill_source', '')))
    for r in htr:
        T.append(dict(t=r['time_utc'], sleeve='HBO ' + r['sub'], coin=r['coin'], act=r['action'], side=r['side'], px=r['price'], nt=r['notional_usd'], real=r['realized_pnl_usd'], why=r['reason'], slip=r['slippage_usd'], src=r['fill_source']))
    T.sort(key=lambda x: x['t'], reverse=True)
    ACT = {'open': 'Entry', 'close': 'Exit', 'increase': 'Add', 'reduce': 'Reduce'}
    WHY = {'stop': 'Stop-loss (3 ATR)', 'stop_loss': 'Stop-loss (6 ATR)', 'time_exit': 'Time exit', 'kill_switch': 'Daily loss limit', 'channel_exit': 'Exit channel', 'universe_exit': 'Left universe',
           'flip': 'Reversal', 'breakout_entry': 'Breakout', 'initial_position': 'Trend already running at start', 'rebalance': 'Resize', 'delisted_or_no_price': 'Delisted / no price'}
    rows = []
    for x in T[:40]:
        isx = x['act'] in ('close', 'reduce'); rl = f(x['real'])
        rows.append('<tr>' + td(brt_str(x['t']) + ' BRT', l=True) + td(e(x['sleeve'])) + td(x['coin']) + td(ACT.get(x['act'], x['act'])) + td(x['side'].capitalize()) + td(px(x['px'])) + td(usd(f(x['nt'])))
                    + td(usd(rl, True) if isx else '—', cls(rl) if isx else '') + td(usd(f(x['slip']))) + td(WHY.get(x['why'], x['why'])) + '</tr>')
    ncl = sum(1 for x in T if x['act'] == 'close'); nw = sum(1 for x in T if x['act'] == 'close' and f(x['real']) > 0)
    H.append('<section class="panel"><h2>Recent trades</h2><p class="section-sub">Last 40 simulated fills, newest first. Realized P&amp;L is price P&amp;L of the closed position before fees. '
             f'Slippage is the adverse distance between the mid (or stop level) and the fill. Closed trades so far: {ncl}' + (f', {nw / ncl * 100:.0f}% profitable.' if ncl else '.') + '</p>')
    H.append(table(['Time', 'Sleeve', 'Coin', 'Action', 'Side', 'Price', 'Notional', 'Realized P&L', 'Slippage', 'Reason'], rows, 'No trades yet.') + '</section>')
    # ---- rules ----
    H.append('<section class="panel"><h2>Strategy rules</h2><ul class="rules">'
             '<li><b>Donchian 55 (daily).</b> Entry on a 55-day channel breakout, exit on the opposite 27-day channel, 3-ATR trailing Stop-loss at the daily close. Position size targets 35% annual portfolio volatility, at most 3× gross Leverage and 25% of the sleeve per coin. Universe: top 40 perps by 30-day dollar volume.</li>'
             '<li><b>HBO ensemble (hourly).</b> Universe: top 20 perps by 30-day dollar volume, ranked point-in-time at the previous daily close. Orders are filled when the hourly bar closes, at the live price of the next run. Risk 0.5% of sub-account per trade, max 8 positions and 3× gross per sub-account, 3% daily loss limit.</li>'
             '<li><b>Execution realism.</b> Fills use the live mid plus real order-book Slippage (HBO: the larger of the real book and a 3/4/6 bp assumption by liquidity rank). Donchian assumes 70% maker fills at 0.015% and 30% taker at 0.045%; HBO is all taker at 0.045%. Sizes respect each coin\'s size decimals, the US$ 10 minimum order and per-coin maximum Leverage (initial margin ≤ 80% of equity). Stale data stops the run.</li>'
             '<li><b>Funding.</b> Real hourly Hyperliquid funding is charged on every open position (shorts receive when the rate is positive).</li>'
             '<li><b>Rebalance.</b> Once a month the two sleeves are reset to 50/50 by moving virtual cash (no trading cost).</li></ul></section>')
    LIVE_END = len(H)
    # ---- backtest ----
    rows_ref = {r['key']: r for r in ref.get('rows', [])}
    if rows_ref:
        H.append('<section class="panel"><h2>Backtest evidence (simulated, 1 Sep 2023 to 25 Nov 2026, US$ 10,000.00 start)</h2><p class="section-sub">'
                 'Same rules as this portfolio on historical data (Hyperliquid daily data and funding; hourly prices from a validated Binance perpetual proxy for HBO, correlation 0.998). '
                 'The first row is the earlier figure; the second is the corrected base case after the audit (no idle-cash yield, monthly rebalance); the rest are stress tests. '
                 'Parameters were tuned on 2023 to 2025; 2026 is out-of-sample for the strategy rules. Past and simulated results do not guarantee future results.</p>')
        order = ['reported', 'corrected', 'daily', 'bh', 'late', 'x2', 'worst', 'strict', 'A', 'B']
        rr = []
        for k in order:
            r = rows_ref.get(k)
            if not r: continue
            b = '<b>' if k == 'corrected' else ''; eb = '</b>' if k == 'corrected' else ''
            rr.append('<tr>' + td(f"{b}{e(r['name'])}{eb}", l=True) + td(usd(r['final'])) + td(f"{r['moic']:.3f}×") + td(pct(r['irr'] * 100, dec=1)) + td(pct(r['maxdd'] * 100, sign=False, dec=1), 'neg')
                      + td(f"{r['sharpe']:.2f}") + ''.join(td(pct(r[f'y{y}'] * 100, dec=1), cls(r[f'y{y}'])) for y in (2023, 2024, 2025, 2026)) + '</tr>')
        H.append(table(['Scenario', 'Final equity', 'MOIC', 'IRR (annualized)', 'Max drawdown', 'Sharpe', '2023 (from Sep)', '2024', '2025', '2026 (to 25 Nov)'], rr))
        H.append('<p class="rule-note">MOIC = final equity ÷ invested capital. IRR is the annualized return of a single US$ 10,000.00 contribution with no withdrawals (1,182 days). Max drawdown is measured on daily closes.</p>')
        mc = ref.get('monthly_corrected', {})
        if mc:
            by = {}
            for k, v in mc.items(): y, m = k.split('-'); by.setdefault(y, {})[int(m)] = v * 100
            years = sorted(by); mn = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            mr = ['<tr>' + td(n, l=True) + ''.join(td(pct(by[y][i], dec=1), cls(by[y][i])) if i in by[y] else td('—') for y in years) + '</tr>' for i, n in enumerate(mn, 1)]
            H.append('<h3>Monthly returns · corrected base case</h3><p class="section-sub">November 2026 is through the 25th.</p>' + table(['Month'] + years, mr))
        H.append('<h3>Backtest equity</h3><p class="section-sub">Earlier figure (grey) versus corrected base case (navy). The dashed vertical line marks the start of 2026.</p>')
        H.append(svg_lines([('Earlier figure', '#9aa3b5', ref['series']['reported']), ('Corrected base case', '#0f1f4b', ref['series']['corrected'])], baseline=10000, vline=('2026-01-01', '2026')))
        H.append('</section>')
    H.append('<section class="panel"><h2>What the audit checked</h2><ul class="rules">'
             '<li><b>Universe.</b> Both legs rank coins point-in-time by trailing 30-day volume at the previous close. HBO covers 97.6% of top-20 coin-days (the rest have no proxy history); re-running with the strict top 20 of all Hyperliquid perps gave a similar result. Coins fully removed from the API are missing from the Donchian history (small residual survivorship).</li>'
             '<li><b>Look-ahead.</b> Signals were re-computed on truncated histories and matched exactly: no future data is used. Orders act on the next bar.</li>'
             '<li><b>Costs and funding.</b> Both legs include real hourly funding. Doubling costs, doubling funding and delaying HBO fills are in the table above.</li>'
             '<li><b>Combo.</b> The earlier figure assumed free daily rebalancing and a hypothetical 4% yield on idle cash for the Donchian leg only. Both were removed in the base case.</li>'
             '<li><b>Live engine.</b> The HBO paper engine replays the backtest bar by bar with identical results (difference below US$ 0.000001 over 4,272 hourly bars on Hyperliquid candles).</li></ul></section>')
    if page == 'live': H = H[:LIVE_END]
    elif page == 'meme':
        from meme_tab import MEME_SECTION
        H = H[:2] + [MEME_SECTION]
        H.append('</main><footer class="foot">Paper simulation for research only, not investment advice. Meme desk data: DEX Screener, GeckoTerminal, RugCheck and GoPlus public APIs; no wallet, key or order is used. Times in BRT (America/Sao_Paulo).</footer></body></html>')
        return ''.join(H)
    else:
        H = H[:2] + ['<section class="intro"><h2>Backtest evidence</h2><p>Historical simulation of the same rules as the live portfolio. This is research evidence, not live results. The live paper portfolio is on the first tab.</p></section>'] + H[LIVE_END:]
    H.append('</main><footer class="foot">Paper simulation for research only, not investment advice. Data: Hyperliquid public info API (candles, mids, order books and funding history). '
             'Times in BRT (America/Sao_Paulo); daily candles close at 00:00 UTC = 21:00 BRT. Past and simulated results do not guarantee future results.</footer></body></html>')
    return ''.join(H)

def build_to(base, out, now_ms, info=None, mids=None):
    for pg, path in (('live', out), ('meme', os.path.join(os.path.dirname(out), 'meme.html')), ('backtest', os.path.join(os.path.dirname(out), 'backtest.html'))):
        page = build(base, now_ms, info, mids, pg)
        tmp = path + '.tmp'; open(tmp, 'w', encoding='utf-8').write(page); os.replace(tmp, path)
