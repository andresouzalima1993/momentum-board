"""Public, read-only Hyperliquid info API helpers (no keys, no wallets, no orders)."""
import time, requests
API = 'https://api.hyperliquid.xyz/info'
HOUR = 3600000; DAY = 86400000
def post(body, tries=6):
    for k in range(tries):
        try:
            r = requests.post(API, json=body, timeout=30)
            if r.status_code == 429: time.sleep(8 * (k + 1)); continue
            r.raise_for_status(); return r.json()
        except requests.RequestException:
            time.sleep(4 * (k + 1))
    raise RuntimeError(f'API failed: {body.get("type")}')
def fetch_meta():
    m = post({"type": "meta"})
    return {a['name']: dict(szDecimals=int(a['szDecimals']), maxLeverage=float(a['maxLeverage']), delisted=bool(a.get('isDelisted'))) for a in m['universe']}
def fetch_mids(): return {k: float(v) for k, v in post({"type": "allMids"}).items()}
def candles(coin, interval, start_ms, end_ms):
    r = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": int(start_ms), "endTime": int(end_ms)}})
    return r if isinstance(r, list) else []
def funding_history(coin, start_ms, end_ms):
    r = post({"type": "fundingHistory", "coin": coin, "startTime": int(start_ms), "endTime": int(end_ms)})
    return r if isinstance(r, list) else []
class Books:
    """Real l2Book slippage of a taker order against the mid (walks the book)."""
    def __init__(self): self.b = {}
    def slip(self, coin, notional):
        if coin not in self.b:
            try: self.b[coin] = post({"type": "l2Book", "coin": coin})['levels']
            except Exception: self.b[coin] = None
        lv = self.b[coin]
        if not lv or not lv[0] or not lv[1]: return 0.0
        bid = float(lv[0][0]['px']); ask = float(lv[1][0]['px']); mid = (bid + ask) / 2
        need = notional / mid; got = 0.0; cost = 0.0
        for l in lv[1]:
            q = min(need - got, float(l['sz'])); got += q; cost += q * float(l['px'])
            if got >= need - 1e-12: break
        if got < need - 1e-12: cost += (need - got) * float(lv[1][-1]['px'])
        return max(0.0, cost / need / mid - 1) if need > 0 else 0.0

def day_volumes():
    """24h notional volume per perp from metaAndAssetCtxs (used only to pre-filter candidates for the universe)."""
    m, ctx = post({"type": "metaAndAssetCtxs"})
    return {a['name']: float(c.get('dayNtlVlm') or 0) for a, c in zip(m['universe'], ctx) if not a.get('isDelisted')}
