# Changelog

## v0.5.0 (2026-06-17)

### Features

- **Dual scoring models** (`--scoring-model {classic,quant}):
  - New **`quant`** model (default) — Seeking-Alpha-inspired five-factor scoring: Value 25%, Growth 20%, Profitability 25%, Momentum 15%, EPS Revisions 15%
    - Includes disqualifying grades that cap the total score at neutral when a factor falls into red-flag territory
    - EPS Revisions uses the average EPS surprise (Reported EPS vs analyst Estimate) over the last four reported quarters
  - **`classic`** model — Original Graham/Buffett + momentum model: Quality 35%, Value 25%, Health 20%, Trend 20%
  - Selectable via CLI (`--scoring-model`), API (`scoring_model=...`), backtest, and advisor subcommand
  - `quant` is now the project-wide default based on backtest validation

### Data

- Added `return_on_assets` to `StockData` and the backtest financial-statement pipeline to support the `quant` Profitability factor

### Validation

- Backtest comparison (US universe, 5y, top 5, quarterly snapshots, min-market-cap 0):
  - `classic`: Alpha -2.56%, Sharpe 0.36, Win Rate 12M 53.3%
  - `quant`: Alpha **4.50%**, Sharpe **0.58**, Win Rate 12M **66.7%**
  - Verdict: **IMPROVED** — higher alpha, Sharpe, and 12M win rate

### Tests

- Added `tests/test_scoring_quant.py` covering the `quant` model: factor breakdown, disqualifying grades, default behavior, technical blending, and classic backwards compatibility
- Updated existing tests to reflect `quant` as the new default

### Changed

- **`--include-technical` default is now model-dependent** — enabled by default for the `quant` model (based on backtest validation showing broad US improvements) and disabled by default for `classic`. Added `--no-include-technical` to force-disable, and `resolve_include_technical()` helper to centralize the default logic across CLI, API, recommender, backtest, advisor, and HTML export.

### Fixes

- **Dividend yield normalization** — `yfinance` reports `dividendYield` inconsistently (decimal for most US tickers, already-multiplied percentage for some European tickers). Added `_sanitize_yield()` to divide any value greater than `1.0` by 100, ensuring the new **Yield** column is always displayed as a correct percentage.
- **Reliable stock dividend yield** — `yfinance`'s `dividendYield` field can be outright wrong for some tickers (e.g. AAPL ~36%, V ~80%). Stock yield is now computed as trailing-twelve-month dividends from `Ticker.dividends` divided by the current price, with `_sanitize_yield(info["dividendYield"])` as a fallback when raw dividends are unavailable. Verified live: AAPL now ~0.35%, V now ~0.79%.
- **Fix `quant` + `--include-technical` score calculation** — the total score was computed before blending RSI/MACD into the Momentum factor, so technical indicators had no effect on rankings. The total is now recomputed after the technical blend, and the updated backtest numbers for the "When to use technical indicators" section reflect the corrected behavior.

### UI

- New **Yield** column in both CLI Rich table and HTML export, placed between **P/E** and **1M Δ**. Shows `dividend_yield` for stocks and `yield_` for ETFs; sortable in the HTML report.

### Tests

- Added `tests/test_data_source.py::TestSanitizeYield` covering decimal, percentage, zero, and non-finite yield inputs
- Added `tests/test_data_source.py::TestTtmDividendYield` covering trailing-twelve-month yield calculation and old/empty dividend handling
- Added `tests/test_html_export.py::test_export_html_includes_yield_column` verifying stock and ETF yield rendering

### Docs

- README.md: added dual-model documentation, `--scoring-model` CLI option, scoring-model comparison in `scoring_baseline.py` examples, new **Yield** output column, dividend-yield normalization note, updated "When to use technical indicators" backtest table for the `quant` model, and model-dependent `--include-technical` defaults
- AGENTS.md: documented `classic`/`quant` models, weights, validation workflow, `_sanitize_yield()` convention, `_ttm_dividend_yield()` convention, and `resolve_include_technical()` default rules

## v0.4.1 (2026-06-07)

### Docs

- Backtest validation re-run after alpha formula and snapshot date fixes; all numbers updated in "When to use technical indicators" section
- Baseline JSON files saved to `baseline-results/` for reproducibility

## v0.4.0 (2026-06-07)

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
- **Constant dictionaries moved to module level** in `recommender.py` — `_CURRENCY_TO_REGION` and `_TICKER_ALIASES` are no longer recreated on every `_build_universe()` call, reducing allocation overhead
- **Duplicated history-processing code extracted** in `data_source.py` — `_apply_history_common()` helper eliminates the identical 7-line block that was duplicated between `_fetch_stock()` and `_fetch_etf()`
- **`_trend_metrics()` degrades gracefully** — short histories (<200 bars) no longer discard all metrics; daily_change and 1m return are now computed even when SMA200-dependent metrics (price_vs_sma200, return_12m, slope, volatility) cannot be calculated
- **Fetch and score exceptions separated** in `recommender.py` — distinct log messages (`Failed to fetch %s` vs `Failed to score %s`) make it easier to diagnose whether a ticker is missing due to a network issue or a scoring bug
- **Rate limiting added to DataRoma scraper** — polite `time.sleep(0.5)` between successful manager requests, plus clearer retry-exhaustion logging
- **`fear_greed_index()` moved out of `export_recommendations_html()`** — the caller now fetches and passes the data explicitly, eliminating the side-effect network call inside the rendering function and making the export pure
- **Cache safety improvements** — `close_all()` no longer sets a `_closed` flag that would block legitimate reopening; `clear()` now runs `VACUUM` after `commit()` to reclaim space; `set()` purges expired rows every 100 writes to prevent unbounded growth
- **Universe corrections** (validated with live yfinance data):
  - `asia_universe.py`: Fixed 5 incorrect comments (e.g., `0001.HK` is CKH Holdings not HSBC, `8802.T` is Mitsubishi Estate not Astellas Pharma)
  - `asia_universe.py`: Replaced 3 delisted/low-quality tickers: `1918.HK` (Sunac, penny stock) → `0388.HK` (HKEX), `DXN.AX` (penny stock) → `CSL.AX` (CSL), `C61U.SI` (delisted) → `BN4.SI` (Keppel), `5491.T` (JUKI) → `4503.T` (Astellas Pharma)
  - `asia_etf_universe.py`: Removed 2 delisted ETFs: `YEN`, `EWJD`
  - `superinvestor_universe.py`: Fixed `UHAL.B` (delisted) → `UHAL` (U-Haul Holding)

### Validation

- Backtest comparison of `--include-technical` across 4 scenarios (US $2B+, US no-filter, US mega-caps $200B+, EU $2B+) — re-run after alpha formula and snapshot date fixes:
  - ✅ **Helps** with concentrated mega-cap lists (US $200B+: alpha -11.50% → -5.08%, Sharpe 0.69 → 0.89, drawdown 15.94% → 7.28%)
  - ❌ **Hurts** with broad + quality-filtered universes (US $2B+: alpha 10.32% → 3.01%, Sharpe 1.20 → 1.06; EU $2B+: alpha 4.59% → -1.20%, Sharpe 0.98 → 0.75)
  - ⚠️ **Neutral** for US no-filter (alpha 2.18% → 1.89%, Sharpe 0.45 → 0.47)
  - Recommendation: use `--include-technical` only for concentrated mega-cap lists; avoid with broad screens

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
