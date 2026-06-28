---
name: data-source-switch
description: Guide for switching between yfinance, yahooquery, and FMP data sources
license: MIT
---

# Data Source Switch

## Available sources

| Source | Flag | Best for | Limitations |
|--------|------|----------|-------------|
| **yfinance** | `--data-source yfinance` (default) | Universal, ETFs, backtest | Slower for large universes, rate limits |
| **yahooquery** | `--data-source yahooquery` | Large stock universes, speed | No backtest, no ETFs |
| **FMP** | `--data-source fmp` | When yfinance fails / rate-limited | Requires `FMP_API_KEY`, 250 req/day, no ETFs |

## Key differences

### yahooquery
- Batch fetches in chunks of 10, 5 parallel workers
- Automatic per-ticker fallback to yfinance on failure
- Does not support backtest or ETFs

### FMP
- 4 API calls per ticker (profile, ratios-ttm, historical-price-eod, earnings-surprises)
- Free tier: 250 requests/day ≈ 60 tickers
- Pre-flight rate-limit check, auto-fallback on HTTP 429
- Does not support backtest or ETFs

## Switching

### CLI
```bash
investdaytip -n 5 -r us --data-source yahooquery
investdaytip -n 5 -r us --data-source fmp
investdaytip advisor --data-source yahooquery
investdaytip backtest -n 5 -r us   # always yfinance
```

### Programmatic API
```python
get_recommendations(top_n=5, region="us", data_source="yahooquery")
```

## Common issues

| Issue | Source | Fix |
|-------|--------|-----|
| Rate limited | yfinance | Wait 1-2 min, or switch to yahooquery/FMP |
| Missing `FMP_API_KEY` | FMP | `export FMP_API_KEY=your_key` |
| Batch fails entirely | yahooquery | Falls back to yfinance automatically |
| ETFs not supported | yahooquery/FMP | Use `--data-source yfinance` for ETFs |
