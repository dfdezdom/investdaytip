# InvestDayTip

> A multi-factor analysis tool that suggests long-term **stock & ETF** buy recommendations from US and European markets, computed live from public market data.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## Features

- 📈 **Multi-factor scoring** — composite 0-100 score per asset
- 🏦 **Stocks & ETFs** — auto-detected and scored with dedicated models
- 🌍 **US & European markets** — DAX, CAC 40, FTSE 100, IBEX 35, AEX, SMI, FTSE MIB + S&P 500
- ⚡ **Concurrent fetching** — analyzes ~100 tickers in seconds
- 📊 **Rich CLI output** — price, 1M/1Y change, score breakdown and rationale
- 🧪 **Pure scoring functions** — testable without network

---

## Installation

```bash
git clone https://github.com/dfdezdom/investdaytip.git
cd investdaytip
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

For development (tests included):

```bash
pip install -e ".[dev]"
```

---

## Usage

### CLI

```bash
investdaytip                         # Top 5 from full universe (US + EU + Asia, stocks + ETFs)
investdaytip -n 10                   # Top 10
investdaytip -a stocks               # Only stocks
investdaytip -a etfs                 # Only ETFs
investdaytip -r us                   # Only US
investdaytip -r eu                   # Only Europe
investdaytip -r asia                 # Only Asia
investdaytip -r eu -a stocks         # Only European stocks
investdaytip -r asia -a etfs         # Only Asian ETFs
investdaytip -t AAPL MSFT VOO        # Custom ticker list
investdaytip --workers 20            # More parallelism
investdaytip --help
```

#### Options

| Flag | Description | Default |
|---|---|---|
| `-n, --top N` | Number of recommendations | `5` |
| `-t, --tickers ...` | Custom ticker list (overrides universe) | curated universe |
| `-a, --asset-class {all,stocks,etfs}` | Asset class filter | `all` |
| `-r, --region {all,us,eu,asia}` | Region filter | `all` |
| `--workers N` | Parallel fetch threads | `10` |

### Programmatic API

```python
from investdaytip import get_recommendations

picks = get_recommendations(top_n=5, region="eu", asset_class="stocks")
for s in picks:
    print(f"{s.data.ticker} ({s.asset_type}) — score={s.total:.1f}")
    print(f"  Price: {s.data.current_price} {s.data.currency}")
    print(f"  1M: {s.data.return_1m:.2%}  1Y: {s.data.return_12m:.2%}")
    print(f"  Why: {'; '.join(s.rationale[:3])}")
```

---

## Output

Each recommendation includes:

| Column | Meaning |
|---|---|
| **Type** | `STOCK` or `ETF` |
| **Ticker / Name / Sector** | Identification |
| **Price** | Current price in native currency |
| **1M Δ** | % change vs ~22 trading days ago |
| **1Y Δ** | % change vs ~252 trading days ago |
| **Score** | Composite 0-100 weighted score |
| **Breakdown** | Four sub-scores (see below) |
| **Why** | Top 3 rationale notes |

---

## Scoring Model

Each metric is normalized to **0-100** via piecewise-linear functions over empirically reasonable ranges. Missing data contributes a neutral **50** so a ticker isn't penalized for lacking a metric.

### Stocks (Graham/Buffett + Momentum)

| Pillar | Weight | Metrics |
|---|---|---|
| **Quality** | 35% | ROE, profit margin, earnings & revenue growth |
| **Value** | 25% | trailing P/E, P/B, PEG |
| **Health** | 20% | Debt/Equity, current ratio, free cash flow |
| **Trend** | 20% | price vs SMA200, 12-month return, SMA200 slope |

### ETFs

| Pillar | Weight | Metrics |
|---|---|---|
| **Returns** | 40% | 3y avg, 5y avg, 12m return |
| **RiskAdj** | 25% | Sharpe proxy `(r12 - rf) / σ`, annualized volatility |
| **Size** | 15% | AUM (log scale) |
| **Cost/Yield** | 20% | expense ratio (lower=better), dividend yield |

---

## Universes

When no `-t` is given, InvestDayTip uses curated universes:

- **US stocks** — ~50 large-caps across all S&P sectors (`src/investdaytip/universe.py`)
- **US ETFs** — ~40 broad-market, factor, sector and bond ETFs (`etf_universe.py`)
- **EU stocks** — ~60 large-caps from DAX, CAC, FTSE 100, IBEX, AEX, SMI, FTSE MIB, Nordics (`eu_universe.py`)
- **EU UCITS ETFs** — ~25 broad, sector and bond UCITS ETFs (`eu_etf_universe.py`)
- **Asia stocks** — ~70 large-caps from Japan, Hong Kong, Singapore, India, South Korea, Taiwan, and Australia (`asia_universe.py`)
- **Asia ETFs** — ~30 broad-market, country-specific, and sector ETFs with significant Asian exposure (`asia_etf_universe.py`)

Tickers use Yahoo Finance suffixes: `.DE` Xetra · `.PA` Paris · `.AS` Amsterdam · `.L` London · `.MC` Madrid · `.MI` Milan · `.SW` Swiss.

---

## Data Source

All market data is fetched live from **Yahoo Finance** via the [`yfinance`](https://github.com/ranaroussi/yfinance) library. Fundamentals come from `Ticker.info`, prices and trend metrics from `Ticker.history(period="2y")`.

---

## Project Structure

```
src/investdaytip/
├── __init__.py            # Public API: get_recommendations
├── main.py                # CLI entry point + rich table rendering
├── recommender.py         # Concurrent orchestration
├── data_source.py         # yfinance wrapper + dataclasses (StockData / EtfData)
├── scoring.py             # Pure scoring functions (score_stock, score_etf)
├── universe.py            # US stock universe
├── etf_universe.py        # US ETF universe
├── eu_universe.py         # EU stock universe
├── eu_etf_universe.py     # EU UCITS ETF universe
├── asia_universe.py       # Asia stock universe
└── asia_etf_universe.py   # Asia ETF universe
tests/
├── test_scoring.py        # Stock scoring tests
└── test_etf_scoring.py    # ETF scoring tests
```

---

## Limitations & Caveats

- **No currency normalization** — prices, market caps and AUM stay in native currency
- **`min_market_cap` filter** is applied against raw native-currency figures
- Some European tickers change Yahoo symbols over time; if a ticker is delisted in Yahoo it's silently skipped
- ETF expense ratios are sometimes missing in `yfinance` — the scorer falls back to a slightly optimistic default (60) in that case
- **Long-term, fundamental-driven model**: not suitable for short-term/day trading signals

---

## Testing

```bash
pytest -q
```

The scoring engine is purely functional and tested without network calls.

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Disclaimer

**This is not financial advice.** InvestDayTip is an educational tool that applies a deterministic scoring model to publicly available data. Always do your own research and consult a licensed advisor before making investment decisions.

---

## License

[MIT](LICENSE)
