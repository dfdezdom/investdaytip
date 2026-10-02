# Changelog

## v0.15.1 (2026-10-02)

### Fixes

- **Clean progress output** — per-ticker fallback and mis-scaled-facts messages no longer interleave with the Rich progress bar (they wrote to stderr while the bar redraws in place, fragmenting it into repeated strips). They now log at INFO with the reason preserved, and the aggregate `⚠️ … continuing with yfinance for N tickers` notice is printed once after the bar finishes. Also removes the duplicated "falling back" wording in the mis-scale message.

## v0.15.0 (2026-10-02)

### Features

- **Derived PEG in the Value factor** — `PEG = trailing P/E ÷ earnings growth` (positive growth only; decliners stay neutral), computed identically in every path (backtest, yfinance and StockFit sources). It was the best IC of the Value family (factor-IC +0.055 mean / 86% hit vs P/E −0.029 and P/B −0.011) and it replaces Yahoo's analyst-based `pegRatio`, which was often absurd (BMY: 17.37 against +178% growth; both sources now read 7.5). Validated before/after on the full US universe: **5y alpha 5.75%→6.51%, Sharpe 0.68→0.73; 3y alpha 12.61%→14.54%, Sharpe 1.56→1.84, 12M win rate 75%→100%** — never worse on any metric. **Scores and rankings differ from 0.14.2.**
- **Unified fundamentals across data sources** — the yfinance path now derives growth and ratios from the same statements-based formulas as the backtest builders (instead of Yahoo's differently-defined info fields), with levels on a **TTM basis** (flows summed over the latest four quarters, balances from the latest quarter — matching StockFit's `period=ttm` overlay) and TTM free cash flow from the quarterly cash-flow statement. Comparisons keep their validated fiscal-year basis. Characterized on the top-20 US: cross-source Spearman rank correlation **0.441 → 0.971**, top-10 overlap 6/10 → 9/10, `earnings_growth` diffs 19/20 → 0/20, `current_ratio` 7/20 → 0/20, FCF 16/20 → 4/20.

### Fixes

- **Mis-scaled facts fall back to yfinance** — ADR share classes / issued-vs-outstanding / currency mixes produced garbage absolutes (TSM: 5× market cap and P/E 225; PG: 1.7×). Two guards: an internal `NI ≈ EPS × shares` check in the StockFit fetcher, and a plausibility screen (P/E > 150 or P/B > 60) that cross-checks market cap — both feed the automatic yfinance fallback. Clean tickers pay zero extra calls.
- **StockFit dividends are TTM** — summed from the latest four quarterly payments (some filers report a single quarter's rate as "annual": UNH showed 0.59% yield instead of 2.4%).
- **Thread-safe stderr suppression** — `_suppress_stderr()` swapped the global `sys.stderr` from concurrent fetch threads; the first to exit restored another thread's closed devnull ("I/O operation on closed file"). Nested correctly now, with a regression test.

## v0.14.2 (2026-09-29)

### Fixes

- **HTML recommendation filters now include Fundamental insights** — searching or filtering by asset class, region, minimum score, or return now hides/shows the matching insight summary rows and fiscal-year details at the same time as the main recommendations table. The insights section is hidden if no insight ticker matches.

- **`--data-source stockfit` now enriches missing market cap and EPS surprises from yfinance** — StockFit has no analyst estimates and can omit shares, leaving `eps_surprise=None` and `market_cap=None`. The recommender now fetches yfinance only when either field is missing, fills only those fields (never overwrites StockFit fundamentals), and applies the requested market-cap filter afterward. This prevents large caps such as META from being excluded solely because StockFit omitted shares, and supplies the EPS Revisions factor when Yahoo has surprise data. This adds a yfinance fetch for most StockFit tickers; the existing yfinance cache is reused.

- **`--data-source stockfit` entity stitching could graft a defunct filer** — the fallback kept the *longest* candidate series, so `SNDK` (added to the US universe) stitched to the old SanDisk (CIK 1000180, acquired 2016, last annual period FY2015) instead of the current Sandisk Corp (CIK 2023554, spun off 2025) and scored decade-old fundamentals: Growth read −61% with the TTM overlay (50.0 total, capped) or +2843% without it (80.4), against a sane yfinance 79.0. Candidates now have to pass `_series_is_recent()` (latest fiscal period within 550 days — the widest gap a live annual filer shows) and `load_pit_snapshot()` discards a *stitched* snapshot whose predecessor stopped filing long ago, so a poisoned snapshot self-heals instead of being served for a week. The XOM holdco path is untouched (its predecessor still files).

- **`--data-source stockfit`: growth compared a TTM window against the latest filed year** — the TTM overlay shifted the `*_prev` comparison chain, so `earnings_growth`/`revenue_growth` measured TTM vs the last fiscal year. Whenever no quarter had been filed since the fiscal year closed the two windows are the *same period*, so growth read exactly `0.0`: MSFT (FY2026 filed 29-jul-2026) scored Growth 18 → "growth flagged as disqualifying" → 43.3 vs yfinance's 66.5, despite +31% YoY. Levels (P/E, P/B, ROE, margins, D/E) still read TTM, but the YoY comparisons — growth, the improvement flags and `eps_acceleration` — now read the **as-filed fiscal years** (the displaced figure is parked in `*_asfiled` instead of shifting the chain), the same non-overlapping basis the backtest builders use. MSFT now totals 67.3 vs yfinance's 67.3, with identical improvement flags. The backtest PIT paths are untouched (`*_asfiled` stays `None`).

- **`--data-source stockfit` accepts lowercase tickers** — `-t meta` used to miss StockFit's `lookup/batch` (the API keys its response by the uppercase symbol) and silently fall back to yfinance, which changes rankings since the sources are source-dependent. Ticker case is now normalized at the StockFit layer (live fetch, PIT statements/snapshots, insights, research summary), and the fallback warning logs the underlying reason instead of a bare "fetch failed".

## v0.14.1 (2026-09-27)

### Fixes

- **`--data-source stockfit`: current fundamentals switched to TTM** — the live source served as-filed fiscal-year figures, so a stale 10-K EPS made fast-moving earners look absurdly expensive (MU showed P/E 142.6 while the trailing figure was 24.5 after an earnings explosion). Current fundamentals now come from `financials/income-statement?period=ttm` (flows = last 4 reported quarters, balances and share counts = latest quarter), matching yfinance's trailing semantics — MU P/E 24.5 vs yfinance 24.45. Comparison fields stay as-filed fiscal year, so `earnings_growth`, the improvement flags and `eps_acceleration` read "TTM vs last FY vs prior FY". Per-field graceful degradation: a missing TTM fact keeps its as-filed value and unshifted comparison chain, an empty/failed TTM fetch is retried on the next run (never cached), and a rate limit falls back to yfinance per ticker. Backtest PIT paths are untouched (as-filed, look-ahead-free).

## v0.14.0 (2026-09-27)

### Features

- **StockFit as a fourth live data source** — `--data-source stockfit` scores US stocks straight from SEC filings (US-only: non-US universes are excluded automatically with a banner note, ETFs are rejected like FMP). Requires `STOCKFIT_API_KEY` plus a Starter plan (tier-aware gate), falls back to yfinance per ticker on failure. Fundamentals are **as-filed fiscal-year** figures — characterized against yfinance on 30 US tickers (Spearman rank correlation 0.441, top-10 overlap 6/10): trend fields match to 8 decimals, but `earnings_growth`/`profit_margin`/`roe`/`trailing_pe` differ by design and **rankings are source-dependent**. No analyst estimates exist in StockFit, so `forward_pe`/`peg_ratio`/`eps_surprise` stay `None`.
- **Advisor devil's advocate layer** — the OpenCode advisor agent now produces a bear case for every portfolio review and buy recommendation: keyless risk signals via `deep-dive` (layer 1) plus StockFit footnote tools when the MCP server is authenticated (layer 2 — concentration trends, maturity walls, dilution, insider summaries, governance flags). Never fabricates; includes the balanced view of what does *not* support the bear case.
- **`eps_acceleration` diagnostic** — as-filed EPS growth second derivative derived from three fiscal years (StockFit source and both backtest paths). Tested as an estimates-free fallback for the EPS Revisions factor and **rejected** by factor-IC (−0.059); it is exposed for display use and never scored.

### Performance

- **StockFit source caching** — warm `--data-source stockfit` runs dropped from 2:42 to ~6s on the full US universe: profile + dividends in a 1-day cache entry, statements from the local PIT snapshot when under 7 days old, prices in the shared 15-minute history cache.
- Cleaner CLI startup: the data-source note is folded into the banner (`us (StockFit: US-only)`) and the US-only exclusion log no longer interleaves with the progress bar.

### Fixes

- The deep-dive DCF link-out is **hidden** while StockFit's valuation platform is in early access — advertising a DCF users cannot open would be misleading. The URL stays reserved for the per-ticker deep link once the app launches.

### Docs

- README CLI reference completed and reordered (subcommand overview, full backtest/deep-dive option tables, missing flags) with `deep-dive` documented before `backtest`.

## v0.13.0 (2026-09-27)

### Features

- **`deep-dive` subcommand** — per-ticker deep report combining the InvestDayTip score and factor breakdown, StockFit's earnings snapshot (EPS, margins, ROE/ROIC, FCF, growth, next earnings/filing dates), keyless health diagnostics (Piotroski F-Score with its 9 checks and Altman Z + zone — informative only, never scored), a devil's advocate risk block, and a DCF link-out to StockFit's platform. Rich terminal output plus a self-contained HTML page (`--export-html`).
- **Keyless risk signals — the local devil's advocate layer** — `risk_signals.py` turns data we already have into explicit bear-case bullets (Altman zone, losses, negative FCF, leverage, payout ratio, failed Piotroski checks) sorted by severity. Context only — never scored.
- **Tier-aware degradation — works on every StockFit plan, and without a key at all** — a central capability registry (`pit_statements` keyless, `fundamental_insights` free, `deep_dive_summary` starter, `economic_model` stock, `footnotes` pro) plus automatic plan detection: StockFit exposes no plan endpoint, but gated endpoints answer HTTP 403, so the plan is detected by probing the tier boundaries (cached 1d). Features below the detected plan are omitted with the explicit reason — never fabricated. New `investdaytip stockfit-status` prints the plan and the capability matrix.

### Tooling

- **StockFit MCP server registered** (`https://api.stockfit.io/mcp`, OAuth) — 125 research tools for the OpenCode advisor agent (footnotes, governance flags, insider summaries, ownership).

## v0.12.0 (2026-09-27)

### Features

- **"Fundamentals improving" factor in the quant stock model** — Profitability now weights ROE 30%, profit margin 25%, ROA 15% and a new **YoY-improvement sub-score 30%** (Δgross margin and ΔROA vs the previous fiscal year; neutral 50 when statements are unknown — "unknown" is never "improving"). **Scores and rankings differ from 0.11.0.** Validated with factor-IC (mean IC +0.134 / +0.126, 86% hit rate — best candidates of the whole table) and before/after backtests on the full US universe (top-5, min-cap 0): 5y alpha 6.85%→7.60% with unchanged max drawdown, 3y alpha 7.69%→13.37%, Sharpe 1.16→1.30, 12M win rate 50%→62.5% — never worse on any metric of the secondary config.
- **Live-path consistency** — `fetch_asset(with_improvements=True)` fetches the annual income statement and balance sheet (7-day cache) so live scores carry the same improvement flags as backtests; `--scoring-model classic` skips the two extra calls. All three data paths are covered: live yfinance, classic backtest and StockFit PIT.
- **`financial_health.py`** — pure, tested Piotroski F-Score (9 checks), Altman Z-Score (1968 model + safe/grey/distress zones) and the shared `improvement_flags()` semantics. Piotroski composite and Altman Z were evaluated as scoring factors and **rejected** (mean IC +0.039 / +0.014 = noise on large-caps); the module is kept for planned per-ticker diagnostics.

### Tooling

- **`scripts/factor_ic.py`** — new candidate metrics: `piotroski`, `altman_z` and the 9 individual Piotroski checks (per-snapshot cross-sectional IC vs forward 6M returns).

## v0.11.0 (2026-09-26)

### Features

- **StockFit point-in-time fundamentals for backtests** — new `backtest --pit-source stockfit` sources annual SEC filings with their exact acceptance date (`dateFiled`) instead of a fixed reporting-lag assumption, so every snapshot only sees data that was public that day. US stocks only, per-ticker soft degradation to the classic path, entity-stitching fallback for holdco reorgs (XOM). Opt-in; validated on the full US universe: Sharpe 0.74→0.85, max drawdown 24%→11%, 12M win rate 50%→56%, alpha flat.
- **Local PIT snapshot — PIT backtests without an API key** — `scripts/pit_snapshot.py` builds a per-ticker JSON snapshot under `~/.investdaytip/pit/`, and `fetch_pit_statements()` resolves live StockFit (key) → local snapshot → classic fallback. Built during the Pro trial (196/196 US tickers, 2140 fiscal years, 8.2 MB), so PIT backtests keep working after the key expires; a successful live fetch refreshes the snapshot, failures never wipe it.
- **Opt-in fundamental insights in the HTML report** — `--fundamental-insights` appends a StockFit section with revenue/margin trends, FCF/NI and OCF/NI quality, debt/equity and current ratio, plus a per-ticker fiscal-year grid with trend arrows. US stocks only, 3 Free-tier endpoints, cached 1d, degrades gracefully without a key; flag off by default → byte-identical output.
- **Curated universes expanded and cleaned** — 123 missing large caps added across all pools; dead/renamed symbols replaced (`SPLG`→`SPYM`, `CRH.L`→`CRH`); Celltrion symbol fix, `.BR` exchange mapping, SGOV; 5 micro ETFs pruned (AUM < $500M and turnover < $1M/day); `EWS` added as the Southeast Asia representative.

### Fixes

- **StockFit `lookup/search` rejected punctuated company names (HTTP 400)** — `,` `/` `&` `(` `)` in `searchString` are now stripped (verified live: `"BlackRock, Inc."` → 400, `"BlackRock Inc"` → results), and the entity-stitching path is fully best-effort: a rejected search or failed probe can no longer discard a ticker's by-symbol series (`BLK` went from "no data" to a stitched 12-FY series).

## v0.10.0 (2026-09-25)

### Features

- **12-1 momentum in the quant stock model** — the Momentum factor (15%) now uses the 12-month return *excluding* the most recent month, since the last month is short-term reversal rather than momentum. Falls back to the raw 12m return when `return_1m` is missing. Validated with before/after backtests (26 US mega-caps, 3y + 5y + standard config): alpha +0.5-0.7pp, Sharpe +0.01-0.03, no regressions. **Scores and rankings differ from 0.9.0.**
- **Advisor overhaul** — `macro_regime()` now scores five equally-weighted factors (VIX, yield curve, MOVE, DXY, CNN Fear & Greed) with a ±3 trend modifier, and prints the sub-indicators and 5-day trends in the table. New portfolio analysis: risk tilt, concentration and sector rotation, plus macro result caching between runs.
- **`scripts/factor_ic.py`** — per-snapshot cross-sectional Spearman IC of every factor / candidate metric vs forward 6M returns, so weight changes are diagnosed before they are tuned.
- **OpenCode agent tooling** — `.opencode/` config with the advisor subagent, commands, permissions and skills (incl. the Seeking Alpha XLSX import workflow).

### Fixes

- **28 bugs across all layers** — critical ones: advisor CLI missing `--data-source`/`-n`/`--include-technical` flags; an FMP pre-flight error no longer blocks the automatic yfinance fallback; FMP and yfinance info caches no longer poison each other (isolated `fmp_info` cache key); backtest alpha was ~2x inflated at `interval_months < 6`; HTML/JS injection via raw JSON in `<script>` and `innerHTML`.
- **Distressed companies no longer get a perfect Value score** — negative P/E, P/B, PEG and D/E are treated as missing (neutral 50) instead of clamping to 100, in both the classic and quant models, for stocks and ETFs.
- **mypy clean** — `_fetch_batch_chunk` annotates `data` as `AssetData` (was inferred as `EtfData`).

### Docs

- **README** — 6 FMP endpoints, 335 tests, quant ETF scoring model.
- **AGENTS.md** — release workflow, Seeking Alpha import, factor-IC validation workflow, regenerated scoring baselines.

## v0.9.0 (2026-06-27)

### Features

- **`-t` accepts quoted space-separated tickers** — `-t "AAPL MSFT GOOGL"` now works the same as `-t AAPL MSFT GOOGL`. `_split_ticker_args()` splits any arg element on whitespace and commas before merging.
- **`-n` defaults to ticker count when `-t` is used** — explicit tickers no longer require `-n` to see all of them. The default is now `len(tickers)` when custom tickers are provided, falling back to `5` for universe scans.

## v0.7.1 (2026-06-20)

### Fixes

- **`--min-market-cap` filter disabled with explicit tickers** — when using `-t`/`--tickers-file`, the market cap filter now defaults to `0` (disabled) instead of `$2B`. The assumption is that a user-curated list should not be silently filtered. The `$2B` default still applies when scanning the built-in universe.

## v0.7.0 (2026-06-20)

### Fixes

- **`earnings_dates` duplicate index** — yfinance can return duplicate rows in `earnings_dates` (same quarter indexed twice), causing a `ValueError: cannot reindex on an axis with duplicate labels` in `_compute_eps_surprise`. The DataFrame is now deduplicated via `~index.duplicated(keep='first')` before processing.
- **Superinvestor universe included in US stock pool** — the curated `SUPERINVESTOR_UNIVERSE` tickers (102 quality US stocks) were previously only added to the pool when their region was explicitly listed. They are now included whenever `region="us"` is requested, growing the US stock universe from ~58 to ~134 tickers without requiring the `--superinvestor` flag.

### Features

- **Advisor `--top` flag** — limits the number of recommendations shown in portfolio review output, mirroring the main CLI `-n`/`--top-n` behavior.
- **Startup timestamp** — the CLI now prints the current date and time at startup so the output context is clear in logs or screenshots.

### UI

- **Updated Quick Start CLI screenshot** — reflects the new scoring model output and expanded universe.
- **Refreshed backtest table** — "When to use technical indicators" section updated with re-run results for the expanded US universe (134 tickers) under the `quant` scoring model.

## v0.6.0 (2026-06-20)

### Features

- **EPS Revisions now uses EPS surprise** in the `quant` scoring model:
  - Replaces the previous proxy of `earnings_growth` + `revenue_growth` with the average EPS surprise (Reported EPS vs analyst Estimate) over the last four reported quarters.
  - Differentiates the `Growth` factor (earnings/revenue growth) from the `EPS Revisions` factor (estimate surprises).
  - Falls back to neutral when `earnings_dates` data is unavailable.

### Data

- Added `eps_surprise` to `StockData`.
- Added `earnings_dates` fetch and cache support in both live fetching and the backtest pipeline.
- Added `lxml` as a project dependency because yfinance requires it to parse `earnings_dates`.

### Validation

- Backtest comparison (US universe, 5y, top 5, quarterly snapshots, min-market-cap $2B):
  - `quant` before EPS surprise: Alpha **9.59%**, Sharpe **1.24**, Win Rate 12M **72.7%**, Max Drawdown **9.86%**
  - `quant` with EPS surprise: Alpha **10.02%**, Sharpe **1.19**, Win Rate 12M **54.5%**, Max Drawdown **8.87%**
  - Verdict: **MIXED** — alpha and drawdown improved, but Sharpe and 12M win rate declined.

### Tests

- Added `tests/test_data_source.py` coverage for `_compute_eps_surprise`.
- Added `tests/test_scoring_quant.py` coverage for EPS Revisions scoring.
- Added `tests/test_backtest.py` coverage for historical EPS surprise handling without look-ahead bias.

### Docs

- README.md, AGENTS.md, and CHANGELOG.md updated to describe EPS Revisions as an EPS-surprise signal.

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
