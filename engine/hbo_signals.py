"""HBO signal rules (vendored from the research code so live signals equal the backtest signals).
All values use only completed hourly bars: row i = information at the close of hourly bar i."""
import numpy as np, pandas as pd

def agg_daily(P):
    o = P['o'].resample('D').first(); h = P['h'].resample('D').max(); l = P['l'].resample('D').min(); c = P['c'].resample('D').last()
    n = P['c'].resample('D').count(); bad = n < 24 * 0.75
    return [x.mask(bad) for x in (o, h, l, c)]

def at_close_daily(df, idx):
    x = df.copy(); x.index = x.index + pd.Timedelta(hours=23)
    return x.reindex(idx)

def atr(h, l, c, n):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()]).groupby(level=0).max()
    return tr.rolling(n, min_periods=max(2, n // 2)).mean()

def hbo_side(P, n=48, volx=5.0, stop_atr=6.0, dtrend=20):
    """Returns (side, stop_distance): side in {-1,0,1} per hourly row/coin.
    Long: close > highest high of the previous n hours, this hour's volume > volx x average volume of the previous 2n hours,
    and the last closed daily close is above its 20-day SMA. Short mirrors it. Stop distance = stop_atr x ATR(24h)."""
    idx = P['c'].index; c = P['c']; h = P['h']; l = P['l']
    hi = h.rolling(n, min_periods=n).max().shift(); lo = l.rolling(n, min_periods=n).min().shift()
    v = P['v']; va = v.rolling(n * 2, min_periods=n).mean().shift()
    a = atr(h, l, c, 24)
    o_, dh, dl, dc = agg_daily(P)
    sma = dc.rolling(dtrend).mean()
    dm = at_close_daily(dc > sma, idx).ffill().fillna(False).astype(bool)
    dd = at_close_daily(dc < sma, idx).ffill().fillna(False).astype(bool)
    lg = (c > hi) & (v > volx * va) & dm
    sh = (c < lo) & (v > volx * va) & dd
    side = lg.astype(float).fillna(0) - sh.astype(float).fillna(0)
    return side, stop_atr * a
