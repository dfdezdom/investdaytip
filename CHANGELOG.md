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
- 229 total tests, all passing

### Fixes

- Backtest default `top_n` raised from 5 to 10 based on validation: with the full US universe, top 5 produced negative alpha (-3%) while top 10 delivers positive alpha (+1.1%), better Sharpe (0.45 vs 0.24), and lower max drawdown
- Backtest now **disables cache by default** and restores it afterwards to ensure reproducible results — stale history cache can shift `_latest_common_end()` and produce different snapshot counts
- **`--min-market-cap` filter now works** in the main recommendation flow — previously the parameter was accepted but never applied; assets with missing market cap are excluded when the filter is active
- **Backtest alpha formula corrected** — annualization now uses actual `interval_months` instead of hardcoded 6-month assumption, producing accurate alpha values for quarterly (default) and other intervals
- **Backtest rate-limit retries bounded** — `_fetch_ticker_data()` now retries max 3 times with delays [10, 30, 60]s instead of recursing infinitely, preventing `RecursionError` on persistent rate limits
- **Advisor `-s/--sector` flag now works** via CLI — previously only worked when calling `advisor_main()` directly; the flag was missing from the advisor subparser in `main.py`
- **XSS vulnerability fixed** in HTML export — client-side JS `renderTable()` now escapes all dynamic values via `escapeHtml()` helper before `innerHTML` assignment
- **`_technical_score` normalized to 0-100** — previously returned a 0-40 value with implicit coupling to `_trend_score`; now returns a proper 0-100 score that `_trend_score` multiplies by 0.40, matching the pattern of all other sub-scorers
- **Superinvestor data cached once per run** — `get_superinvestor_data()` is now called once in `recommend()` and passed to each `score_asset()` call, eliminating 200+ redundant SQLite reads and JSON parses per recommendation run
- **`_linear()` guards against NaN/inf** — scoring normalization now rejects non-finite values (NaN, +inf, -inf) via `math.isfinite()`, falling back to neutral default instead of propagating invalid values through the scoring pipeline
- **DataRoma cache corruption handled** — `json.loads()` in `dataroma.py` now wrapped in `try/except (JSONDecodeError, ValueError)` to gracefully handle corrupt cache data and trigger a fresh fetch instead of crashing
- **Unified `_fmt_pct` in HTML export** — merged `_fmt_pct` and `_fmt_pct_str` into a single function that uses `_is_finite_number()` guard, preventing crashes on NaN/inf values and eliminating code duplication
- **`get_recommendations()` API now exposes `sector` and `include_technical`** — programmatic API users can now filter by sector and include technical indicators, matching the underlying `recommend()` capabilities
- **Cross-universe aliases expanded** — added `RIO.AX`→`RIO.L` (Rio Tinto) and `ASML.AS`→`ASML` (ASML) to prevent duplicate fetches when multiple regions are merged
- **42 new tests** covering: `get_recommendations()` public API (3 tests), `_safe_get()` NaN/inf rejection (9 tests), `_first()` fallback chain (4 tests), `_linear()` and `_clamp()` scoring primitives (16 tests), ETF fetch path including expense ratio fallback and Sharpe proxy (3 tests), HTML export with superinvestor/technical columns (2 tests), cross-universe aliases (5 tests)
- **Removed duplicated currency filter test** from `test_integration.py` (already covered in `test_recommender.py`)
- **Backtest snapshot date generation fixed** — `_generate_snapshot_dates()` now uses `divmod` to correctly handle any `interval_months` value (previously crashed with `interval_months >= 13`), and preserves end-of-month semantics using `calendar.monthrange` instead of drifting to day 28
- **Python 3.10 ISO-8601 timestamp parsing fixed** — `sentiment.py` now normalizes "Z" suffix to "+00:00" before calling `datetime.fromisoformat()`, which doesn't support "Z" in Python 3.10
- **`run_comprehensive()` no longer aborts on missing portfolio** — portfolio review errors are logged to `result["errors"]` but recommendations are still generated, making the function more resilient
- **Advisor superinvestor warm-up skipped for ETFs** — when `--asset-class etfs` is specified, the ~80 HTTP requests for superinvestor data are now skipped since superinvestor data is only relevant for stocks
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
  - Added `test_recommender.py` tests for cross-universe aliases (single vs multi-region)
- `asia_universe.py`: replaced `M44U.SI` (Mapletree Logistics Trust, $6.0B) with `S68.SI` (Singapore Exchange, $23.3B)
  - Verified all Asia universe tickers now have market cap >=$10B
- README.md: added "Ticker normalization" note under Data Source section explaining GOOGL/GOOG deduplication in DataRoma pipeline

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
