# Momentum paper board

Simulation only. Reads public Hyperliquid market data (candles, mids, order books, funding). Never sends orders, never uses wallets or keys.

- `engine/run_all.py` hourly orchestrator (GitHub Actions cron, minute 07 UTC): Donchian 55 sleeve once per UTC day, HBO ensemble sleeve every hour, monthly 50/50 rebalance, dashboard.
- `data/` state and logs (the only persistent storage).
- `index.html` the dashboard (GitHub Pages).
