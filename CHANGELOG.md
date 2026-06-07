# Changelog

## v0.3.1 (2026-06-07)

### Features

- **Technical analysis indicators** (`--include-technical`): opt-in RSI-14 + MACD histogram integrated into the Trend pillar
  - RSI is inverted (lower = better entry) with a floor at 20 to avoid rewarding stocks in free-fall
  - MACD histogram normalized by price for cross-ticker comparability
  - When enabled, RSI and MACD columns appear in both CLI Rich table and HTML export
  - Backtest baseline runner (`scripts/scoring_baseline.py`) supports `--include-technical` for before/after comparison

### Tests

- 6 new tests for technical indicator computation and scoring behavior
- 176 total tests, all passing

### Fixes

- Backtest default `top_n` raised from 5 to 10 based on validation: with the full US universe, top 5 produced negative alpha (-3%) while top 10 delivers positive alpha (+1.1%), better Sharpe (0.45 vs 0.24), and lower max drawdown
- **Universe corrections** (validated with live yfinance data):
  - `asia_universe.py`: Fixed 5 incorrect comments (e.g., `0001.HK` is CKH Holdings not HSBC, `8802.T` is Mitsubishi Estate not Astellas Pharma)
  - `asia_universe.py`: Replaced 3 delisted/low-quality tickers: `1918.HK` (Sunac, penny stock) → `0388.HK` (HKEX), `DXN.AX` (penny stock) → `CSL.AX` (CSL), `C61U.SI` (delisted) → `BN4.SI` (Keppel), `5491.T` (JUKI) → `4503.T` (Astellas Pharma)
  - `asia_etf_universe.py`: Removed 2 delisted ETFs: `YEN`, `EWJD`
  - `superinvestor_universe.py`: Fixed `UHAL.B` (delisted) → `UHAL` (U-Haul Holding)

### Validation

- Backtest comparison of `--include-technical` across 4 scenarios (US full, US mega-caps, US filtered $2B, EU full):
  - ✅ **Helps** with concentrated mega-cap lists (12 tickers: alpha 4.19% → 7.12%, Sharpe 0.50 → 0.61)
  - ❌ **Hurts** with broad + quality-filtered universes (US $2B filter: alpha 5.04% → 1.49%, Sharpe 1.20 → 1.06)
  - ⚠️ **Neutral/mixed** for broad US and EU universes
  - Recommendation: use `--include-technical` only for small, liquid custom ticker lists; avoid with broad screens

### Docs

- README updated with `--include-technical` flag, RSI/MACD output columns, and Trend pillar description
- README added "When to use technical indicators" section with backtest-driven guidelines
- README added "Market Cap Classification" section explaining the $2B default and size categories
- CLI `--min-market-cap` help text updated to reference the Market Cap Classification section in README
- README "OpenCode AI Agent" section enriched and moved to a prominent position under Usage with prerequisites, capabilities, quick example, and comparison table vs CLI advisor
- AGENTS.md updated with `--include-technical` validation note
- Backtest examples and `scoring_baseline.py` docs updated to reflect new default `top_n=10`
- DataRoma pipeline: GOOG holdings merged into GOOGL to avoid duplicate counting of Alphabet positions
  - Merge logic applied in both `fetch_superinvestor_universe()` and `get_superinvestor_data()` for defense against stale/corrupted cache
  - Invalidated superinvestor cache (key v2) to force re-fetch with unified tickers
  - `superinvestor_universe.py`: removed duplicate GOOG, kept only GOOGL
  - Added `tests/test_dataroma.py` with 9 mocked tests covering GOOG merge, min_overlap filtering, sorting, and malformed ticker filtering
- `superinvestor_universe.py`: removed 12 mid-cap tickers (<$10B market cap) to align with large-cap quality criteria
  - Removed: ABM ($2.5B), ACHC ($2.3B), CROX ($5.9B), HCC ($5.3B), LAD ($6.6B), NCLH ($8.6B), NVST ($3.8B), OMF ($6.4B), OSK ($8.1B), PPLI ($3.1B), SLM ($4.2B), TDS ($4.5B)
  - Verified all 102 remaining tickers have market cap >=$10B
- `recommender.py`: added ticker alias mapping for cross-universe deduplication
  - `2330.TW` (Asia) -> `TSM` (Superinvestor) — Taiwan Semiconductor
  - `9988.HK` (Asia) -> `BABA` (Superinvestor) — Alibaba
  - `RACE.MI` (EU) -> `RACE` (Superinvestor) — Ferrari
  - Aliases only applied when multiple universes are merged (e.g., `region=all`), not when a single region is requested
  - Prevents duplicate fetching/scoring of the same company listed on different exchanges
  - Integration tests updated to reflect alias mapping behavior

---

## v0.3.0 (2026-06-06)

### Features

- **ETF support**: full ETF scoring with dedicated weights (returns 40%, risk-adjusted 25%, size 15%, cost/yield 20%)
- **Superinvestor universe**: DataRoma 13F consensus data with manager count column in HTML/CLI output
- **Sector filter** (`-s`/`--sector`): filter universe by sector with case-insensitive prefix matching
- **Shell tab completion**: `argcomplete` integration — `investdaytip --<TAB>` and `investdaytip --region <TAB>`
- **`--version` flag**: display installed version and exit
- **CNN Fear & Greed Index**: integrated into `macro_regime()` composite score
- **Macro regime indicators**: 10Y-2Y yield curve, MOVE bond volatility index, DXY dollar strength alongside VIX
- **CLI help sections**: organized under Main / Filtering / Data / Performance
- **Rich progress bars**: DataRoma superinvestor cache warm-up and ticker fetch progress
- **Graceful Ctrl+C**: clean exit from interactive prompts
- **Price cache TTL**: reduced from 1h to 15min for fresher price data
- **Buy recommendations**: always shown interactively regardless of macro signal

### Fixes

- argcomplete import made optional to avoid crash when not installed
- Worker thread SQLite connections properly released after pool teardown
- DataRoma scraping fixed for HTTP 406 errors and updated holdings page regex
- Superinvestor cache warmed up before scoring so HTML column is populated
- Incorrect and duplicated Asia tickers corrected
- Various correctness bugs in scoring, data fetching, advisor, and HTML export
- Cache deduplication and logging improvements

### Tests

- New test suites: `test_advisor.py`, `test_recommender.py`, `test_universes.py`, `test_sentiment.py`
- Strict network guard in `conftest.py` — no live yfinance calls in tests
- Disabled cache fixture using `tmp_path` for deterministic test runs
- 103 total tests, all passing

### Docs

- README updated with superinvestor, macro regime, sector filter, and tab completion docs
- AGENTS.md synced with new features, weights, conventions, and test notes
- CONTRIBUTING.md updated with lint/type-check commands
- OpenCode advisor agent docs with macro regime interpretation rules

### Chores

- Removed `plan/` directory (tracked in GitHub issues)
- Configured ruff, mypy, pytest settings in `pyproject.toml`
- Pinned dependency upper bounds for stability
- Added opencode GitHub Actions workflow

---

## v0.2.0 (2026-05-28)

- Advisor module: interactive market pulse, portfolio review, buy recommendations
- HTML export: self-contained report with inline CSS/JS and client-side sorting
- Sentiment: CNN Fear & Greed Index via urllib (no yfinance)
- Scoring weights: Stocks (Quality 35%, Value 25%, Health 20%, Trend 20%)
- Asia universe (JP/EU/CN/KR/IN) and US + EU universes
- CLI: `--export-html`, `--no-cache`, `--cache-clear`, `--min-market-cap`, `--currency`
- DataRoma cache warm-up with Rich progress bar
- Expanded test coverage

## v0.1.1 (2026-05-24)

- Fix: various minor bug fixes
- Initial ticker universes and scoring

## v0.1.0 (2026-05-21)

- Initial release: basic stock screening and scoring
- US and EU stock universes
- CLI interface with Rich tables
- SQLite-based caching
- HTML report export
