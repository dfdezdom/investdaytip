# InvestDayTip

<p align="center">
  <img src="logo.svg" alt="InvestDayTip Logo" width="300">
</p>

> A multi-factor analysis tool that suggests long-term **stock & ETF** buy recommendations from US, European, and Asian markets, computed live from public market data.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/investdaytip.svg)](https://pypi.org/project/investdaytip/)
[![CI](https://github.com/dfdezdom/investdaytip/actions/workflows/ci.yml/badge.svg)](https://github.com/dfdezdom/investdaytip/actions/workflows/ci.yml)

---

## Features

- 📈 **Multi-factor scoring** — composite 0-100 score per asset
- 🏦 **Stocks & ETFs** — auto-detected and scored with dedicated models
- 🌍 **US, European & Asian markets** — S&P 500, DAX, CAC 40, FTSE 100, Nikkei 225, Hang Seng, NSE, and more
- 💱 **Currency filter** — narrow by native currency (`USD`, `EUR`, `JPY`, …)
- ⚡ **Concurrent fetching** — analyzes ~300 tickers in seconds
- 📊 **Rich CLI output** — price, 1M/1Y change, score breakdown and rationale
- 🧾 **Self-contained HTML export** — interactive report with filters and sortable columns
- 🧪 **Pure scoring functions** — testable without network
- 🧠 **Interactive advisor** — market pulse, portfolio review, and tailored buy recommendations via the `advisor` subcommand
- 🤖 **AI-powered advisor** — chat with an intelligent investment advisor that analyzes markets, reviews portfolios, and recommends buys — powered by [OpenCode](https://opencode.ai) agents

---

## Installation

### From PyPI (recommended)

```bash
pip install investdaytip
```

### From source (for development)

```bash
git clone https://github.com/dfdezdom/investdaytip.git
cd investdaytip
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
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

investdaytip -r us eu                # US + Europe

investdaytip -r eu -a stocks         # Only European stocks

investdaytip -r asia -a etfs         # Only Asian ETFs

investdaytip -c USD                  # Only USD-denominated assets

investdaytip -c EUR                  # Only EUR-denominated assets

investdaytip -c USD EUR              # USD + EUR assets

investdaytip -c JPY                  # Only JPY-denominated assets

investdaytip -r eu -c USD            # EU region + USD post-filter

investdaytip -r us eu -c USD EUR     # US + EU, USD + EUR assets
investdaytip -t AAPL MSFT VOO        # Custom ticker list

investdaytip --tickers-file tickers.txt # Custom ticker file (lines, spaces, commas)
investdaytip --tickers-file tickers-files-examples/semiconductors_relevant_tickers.txt

investdaytip --export-html report.html # Export report with interactive filters
investdaytip --export-html             # Uses investDayTip-aaaammdd-hhmm.html
                                       # or investDayTip-<tag>-aaaammdd-hhmm.html
                                       # when --tickers-file is set

investdaytip --workers 20            # More parallelism
investdaytip --min-market-cap 1B     # Raise min market cap to $1B
investdaytip --min-market-cap 0      # Disable market-cap filter
investdaytip --help

./preview.sh                         # Serve generated HTML files on localhost:8000
```

#### Options

| Flag | Description | Default |
|---|---|---|
| `-n, --top N` | Number of recommendations | `5` |
| `-t, --tickers ...` | Custom ticker list (overrides universe) | curated universe |
| `--tickers-file PATH` | Text file with custom tickers (merged with `--tickers` if both are used) | disabled |
| `-a, --asset-class {all,stocks,etfs}` | Asset class filter | `all` |
| `-r, --region {all,us,eu,asia}` `nargs="+"` | Region filter(s) — e.g. `-r us eu` | `all` |
| `-c, --currency {all,USD,EUR,GBP,…}` `nargs="+"` | Currency filter(s); narrows universe to matching regions when no `-r` is given | `all` |
| `--export-html [PATH]` | Export recommendations to self-contained HTML (`investDayTip-aaaammdd-hhmm.html` if omitted) | disabled |
| `--min-market-cap VALUE` | Minimum market cap (`1B`, `500M`, `0` to disable) | `2B` |
| `--workers N` | Parallel fetch threads | `10` |

### Advisor subcommand

Interactive market analysis, portfolio review, and buy recommendations:

```bash
investdaytip advisor                          # Interactive mode (asks for risk, region, etc.)
investdaytip advisor --risk moderate          # Non-interactive with risk preset
investdaytip advisor --risk aggressive -r us -a stocks    # US stocks, aggressive, non-interactive
investdaytip advisor --risk moderate --portfolio portfolios/portfolio.txt  # Custom portfolio
```

See `investdaytip advisor --help` for all options.

### OpenCode AI Agent (chat mode)

Instead of memorizing flags, talk to an **AI investment advisor** that understands your portfolio, checks market fear, and suggests buys — powered by [OpenCode](https://opencode.ai).

#### Usage

Once configured, invoke the advisor from the project root:

```text
@advisor  what's the market pulse?
```

The agent will greet you, present options, and **never run analysis without your confirmation**. Full conversational flow:

```
1. You: @advisor
2. Agent: "What would you like? Market pulse / Portfolio review / Buy recs / Full analysis?"
3. You: "Market pulse, moderate risk"
4. Agent: Runs VIX/VXN check, bubble risk, bubble burst signals → presents results
```

See the [agent definition](.opencode/agents/advisor.md) for full details on capabilities and interpretation.

#### Options

| Flag | Description | Default |
|---|---|---|
| `--risk {conservative,moderate,aggressive}` | Risk profile (if omitted, asked interactively) | interactive |
| `--portfolio PATH` | Path to portfolio ticker file | `portfolios/portfolio.txt` |
| `-a, --asset-class {all,stocks,etfs}` | Asset class filter for buy recommendations | interactive |
| `-r, --region {all,us,eu,asia}` `nargs="+"` | Region filter(s) for buy recommendations | interactive |
| `-c, --currency {all,USD,EUR,GBP,…}` `nargs="+"` | Currency filter(s) | interactive |

### HTML export

Generate an interactive report that works offline (single file with inline CSS/JS):

```bash
investdaytip -n 25 -a all -r all --export-html investdaytip-report.html
```

The generated report includes filters for:

- Text search (ticker/name/sector)
- Asset class (`stock` / `etf`)
- Region (`us` / `eu` / `asia`)
- Minimum score
- Minimum 1M return (%)
- Minimum 1Y return (%)

It also includes:

- Click-to-sort columns (ascending/descending)
- Full-width responsive layout (uses available browser width)
- Pre-rendered rows + client-side interactivity (works even if JS is restricted)
- Direct platform links in table columns: `Ticker` (Google Finance), `T` (TradingView), `Y` (Yahoo Finance)

#### Google Finance exact links

The `Ticker` column uses exact Google Finance quote URLs when a ticker suffix is recognized
(format: `https://www.google.com/finance/quote/SYMBOL:EXCHANGE?hl=en`).

Current suffix mapping includes:

- US (no suffix) → `NASDAQ` by default, with explicit overrides for known symbols (for example `HWM` → `NYSE`)
- Germany `.DE` → `ETR`, `.F` → `FRA`
- France `.PA` → `EPA`
- Netherlands `.AS` → `AMS`
- UK `.L` → `LON`
- Spain `.MC` → `BME`
- Italy `.MI` → `BIT`
- Switzerland `.SW` → `SWX`
- Sweden `.ST` → `STO`
- Denmark `.CO` → `CPH`
- Finland `.HE` → `HEL`
- Norway `.OL` → `OSL`
- Japan `.T` → `TYO`
- Hong Kong `.HK` → `HKG`
- Singapore `.SI` → `SGX`
- India `.NS` → `NSE`
- South Korea `.KS` → `KRX`
- Taiwan `.TW` → `TPE`
- Australia `.AX` → `ASX`
- Canada `.TO` → `TSE`
- China `.SS` → `SHA`, `.SZ` → `SHE`

When a suffix is not mapped, the report falls back to Google Finance search URL.

TradingView links use the same exchange mapping logic.

### Programmatic API

```python
from investdaytip import get_recommendations

# Get top 5 Asian stocks
picks = get_recommendations(top_n=5, region="asia", asset_class="stocks")
for s in picks:
    print(f"{s.data.ticker} ({s.asset_type}) — score={s.total:.1f}")
    print(f"  Price: {s.data.current_price} {s.data.currency}")
    print(f"  1M: {s.data.return_1m:.2%}  1Y: {s.data.return_12m:.2%}")
    print(f"  Why: {'; '.join(s.rationale[:3])}")

# Or mix regions and asset classes
picks = get_recommendations(top_n=10, region="all")  # US + EU + Asia stocks & ETFs
```

---

## Output

Each recommendation includes:

| Column | Meaning |
|---|---|
| **Type** | `STOCK` or `ETF` |
| **Ticker / Name / Sector** | Identification |
| **Ticker link** | Opens Google Finance in a new tab |
| **T / Y** | Opens TradingView / Yahoo Finance in a new tab |
| **Price** | Current price in native currency |
| **P/E** | Trailing price-to-earnings ratio (stocks; `-` when unavailable) |
| **1M Δ** | % change vs ~22 trading days ago |
| **1Y Δ** | % change vs ~252 trading days ago |
| **Score** | Composite 0-100 weighted score |
| **Breakdown** | Four sub-scores (shown in a compact single line) |
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

- **US stocks** — 58 large-caps across all S&P sectors (`src/investdaytip/universe.py`)
- **US ETFs** — 41 broad-market, factor, sector and bond ETFs (`etf_universe.py`)
- **EU stocks** — 65 large-caps from DAX, CAC, FTSE 100, IBEX, AEX, SMI, FTSE MIB, Nordics (`eu_universe.py`)
- **EU UCITS ETFs** — 38 broad, sector and bond UCITS ETFs (`eu_etf_universe.py`)
- **Asia stocks** — 76 large-caps from Japan, Hong Kong, Singapore, India, South Korea, Taiwan, and Australia (`asia_universe.py`)
- **Asia ETFs** — 24 broad-market, country-specific, and sector ETFs with significant Asian exposure (`asia_etf_universe.py`)

Tickers use Yahoo Finance suffixes: 
- **US:** no suffix (AAPL, MSFT)
- **EU:** `.DE` Xetra · `.PA` Paris · `.AS` Amsterdam · `.L` London · `.MC` Madrid · `.MI` Milan · `.SW` Swiss
- **Asia:** `.T` Tokyo · `.HK` Hong Kong · `.SI` Singapore · `.NS` NSE India · `.KS` Korea · `.TW` Taiwan · `.AX` Australia

### Ticker File Examples

The repository includes ready-to-use ticker sets in `tickers-files-examples/` for thematic analyses:

- `artificial_intelligence_relevant_tickers.txt`
- `biotech_relevant_tickers.txt`
- `energy_relevant_tickers.txt`
- `eu_etfs_relevant_tickers.txt`
- `financial_relevant_tickers.txt`
- `health_relevant_tickers.txt`
- `pharma_relevant_tickers.txt`
- `quantum_computing_relevant_tickers.txt`
- `semiconductors_relevant_tickers.txt`
- `space_relevant_tickers.txt`
- `spanish_relevant_tickers.txt`
- `technology_relevant_tickers.txt`

Example:

```bash
investdaytip -n 15 --tickers-file tickers-files-examples/artificial_intelligence_relevant_tickers.txt --export-html
```

After export, preview the generated file in a browser by running:

```bash
./preview.sh
```

Then open the generated report from:

```text
http://localhost:8000/<generated-file-name>.html
```

---

## Data Source

All market data is fetched live from **Yahoo Finance** via the [`yfinance`](https://github.com/ranaroussi/yfinance) library. Fundamentals come from `Ticker.info`, prices and trend metrics from `Ticker.history(period="2y")`.

---

## Project Structure

```
preview.sh                # Local static server for generated HTML reports
src/investdaytip/
├── __init__.py            # Public API: get_recommendations
├── main.py                # CLI entry point + rich table rendering
├── advisor.py             # Interactive advisor: market pulse, portfolio review, buy recs
├── html_export.py         # Self-contained HTML report exporter
├── recommender.py         # Concurrent orchestration
├── data_source.py         # yfinance wrapper + dataclasses (StockData / EtfData)
├── scoring.py             # Pure scoring functions (score_stock, score_etf)
├── universe.py            # US stock universe
├── etf_universe.py        # US ETF universe
├── eu_universe.py         # EU stock universe
├── eu_etf_universe.py     # EU UCITS ETF universe
├── asia_universe.py       # Asia stock universe
└── asia_etf_universe.py   # Asia ETF universe
portfolios/               # Portfolio ticker files
advisor_recommendations/   # Advisor-generated HTML reports (git-ignored)
tests/
├── test_main.py           # CLI helper tests
├── test_html_export.py    # HTML export tests
├── test_scoring.py        # Stock scoring tests
└── test_etf_scoring.py    # ETF scoring tests
tickers-files-examples/
├── semiconductors_relevant_tickers.txt
├── artificial_intelligence_relevant_tickers.txt
├── quantum_computing_relevant_tickers.txt
├── energy_relevant_tickers.txt
├── space_relevant_tickers.txt
├── technology_relevant_tickers.txt
├── spanish_relevant_tickers.txt
├── pharma_relevant_tickers.txt
├── biotech_relevant_tickers.txt
├── health_relevant_tickers.txt
└── financial_relevant_tickers.txt
```

---

## Limitations & Caveats

- **No currency normalization** — prices, market caps and AUM stay in native currency
- **Currency filter** (`-c`) compares against yfinance's reported currency field; tickers with a missing/unknown currency pass only when `-c all`
- **`min_market_cap` filter** (`--min-market-cap`) is applied against raw native-currency figures
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
