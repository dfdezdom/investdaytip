# InvestDayTip — Agent Guide

## Sibling directories (this repo is one of three)

```
/Users/diego/investdaytip-workspace/
├── InvestDayTip/       ← you are here: the engine (MIT, public, PyPI)
├── investdaytip-web/   investdaytip.com (Next.js, proprietary/private). Consumes
│                       this package pinned in scripts/requirements.txt. Never
│                       reimplement scoring there — fix it here and release.
└── stockfit/           decision log (documents, no code): business model, StockFit
                        API evaluation, before/after baselines from experiments
```

**Every `stockfit/...` path in this file is relative to `VSCodeProjects/`, not to
this repo.** Experiment artefacts live under `stockfit/archivo/`. The map of the
whole ecosystem is `VSCodeProjects/AGENTS.md`, which loads automatically.

Two invariants from the decision log that bind this repo:
- StockFit ToS §5 forbids redistributing API data as a standalone dataset.
  Anything exported for public display must stay *slim* — see `snapshot_payload()`
  in `investdaytip-web/scripts/build_ratings.py`.
- Never fabricate a missing value: keep `None` and document why.

## Build / Test / Verify

```bash
source .venv/bin/activate && pip install -e ".[dev]"
python -m investdaytip.main --help     # CLI without install
pytest -q                               # all tests
pytest tests/test_scoring.py -q        # single file
pytest tests/test_scoring.py::test_strong_stock_scores_high -q  # single test
pytest -q -k "strong_stock"            # keyword match
pytest --cov=investdaytip -q           # with coverage
ruff check src tests                    # lint
mypy                                    # type-check (config in pyproject.toml)
./preview.sh                            # HTTP server (localhost:8000) for HTML reports
```

`ruff` + `mypy` are configured in `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`) and
shipped in the `dev` extra. pytest config lives in `[tool.pytest.ini_options]`.
Follow PEP 8 + type hints. Note: `UP` (pyupgrade) is intentionally **off** in ruff —
the convention is `Optional[...]` for dataclass fields, not `X | None`.

## Architecture — Key Structural Facts

| Layer | Path | Role |
|---|---|---|---|---|
| CLI + public API | `main.py` | argparse + `get_recommendations()` re-exported from `__init__.py` |
| Advisor | `advisor.py` | interactive market pulse, portfolio review, buy recs, CLI subcommand |
| Orchestration | `recommender.py` | builds universe, ThreadPoolExecutor fetches, scores, filters, sorts |
| Data fetching | `data_source.py` | yfinance wrapper, dataclasses (`StockData` / `EtfData` / `AssetData`). **All network I/O lives here.** |
| Yahooquery data source | `data_source_yahooquery.py` | yahooquery batch wrapper, maps `all_modules` to yfinance-style `info`, yfinance fallback |
| StockFit PIT source | `data_source_stockfit.py` | StockFit annual statements with `dateFiled` for look-ahead-free backtests (`backtest --pit-source stockfit`); also live insight charts (`--fundamental-insights`) |
| FMP data source | `data_source_fmp.py` | FMP wrapper, `fetch_asset()` alternative, 4 endpoints/ticker, rate-limit auto-fallback to yfinance |
| Caching | `cache.py` | SQLite cache with per-thread connections, WAL mode, write lock |
| Scoring | `scoring.py` | pure functions only — no I/O, no side effects |
| Fundamental health | `financial_health.py` | Piotroski F-Score, Altman Z, YoY-improvement flags — pure functions, no I/O |
| HTML export | `html_export.py` | self-contained report with inline CSS/JS |
| Backtest | `backtest.py` | historical scoring validation (stocks only) |
| Sentiment | `sentiment.py` | CNN Fear & Greed Index, no yfinance (uses `urllib`) |
| Deep-dive report | `deep_dive.py` | Per-ticker report: score + StockFit research-summary + keyless Piotroski/Altman diagnostics |
| Risk signals | `risk_signals.py` | "Devil's advocate" layers (Fase 3): keyless local bullets + StockFit `footnotes/*` bullets — context, never scored |
| Universes | `*_universe.py` (7 modules) | curated ticker lists wired in `recommender._build_universe()` (deduplicated case-insensitively) |
| Tests | `tests/` | 25 test files, no live network calls (autouse network guard in `conftest.py`) |
| OpenCode agent | `.opencode/agents/advisor.md` | advisor subagent: permissions, interactive flow, execution methods, and interpretation guide |

Data flow: `CLI → recommender → data_source (yfinance|yahooquery|fmp) → scoring → html_export / Rich table`

## Yahooquery Data Source (`--data-source yahooquery`)

Yahooquery uses Yahoo's internal API endpoints (not HTML scraping) and supports efficient batch fetching. It is selectable via `--data-source yahooquery`.

| Aspect | Value |
|---|---|
| Batch size | `_CHUNK_SIZE = 10` tickers per chunk |
| Parallel workers | `_MAX_CHUNK_WORKERS = 5` |
| Fallback | Automatic per-ticker fallback to yfinance on failure |
| Cache | Reuses existing SQLite cache (`info` + `history`) |
| Backtest support | **No** — yahooquery does not expose historical financial statements |

### Why use yahooquery?

- More resilient to Yahoo HTML changes than yfinance.
- Faster for large universes via batch fetching.
- Provides a working fallback when yfinance rate-limits or breaks.

### Key field normalizations

The nested `all_modules` response is flattened into the same `info` dict that `_fetch_stock()` / `_fetch_etf()` consume.

| Flat info key | yahooquery source path | Normalization |
|---|---|---|
| `trailingPE` | `summaryDetail.trailingPE` | none |
| `forwardPE` | `summaryDetail.forwardPE` | none |
| `priceToBook` | `defaultKeyStatistics.priceToBook` | none |
| `pegRatio` | `defaultKeyStatistics.pegRatio` | none |
| `returnOnEquity` | `financialData.returnOnEquity` | none |
| `profitMargins` | `defaultKeyStatistics.profitMargins` | none |
| `debtToEquity` | `financialData.debtToEquity` | passed through (same scale as yfinance) |
| `annualReportExpenseRatio` | `fundProfile.feesExpensesInvestment.annualReportExpenseRatio` | ×100 (decimal → percentage) |
| `epsSurprise` | `earnings.earningsChart.quarterly[*].surprisePct` | averaged over available quarters; stored in `info` for cache survival |

### Fallback behavior

1. Tickers are fetched in chunks of 10 via `fetch_batch_yq()`.
2. If a single chunk fails entirely, every ticker in that chunk is marked as failed.
3. Failed / missing tickers are collected as `leftovers` and re-fetched with `fetch_asset()` (yfinance) in `recommender.py`.
4. No user prompt is required; the fallback is automatic.

### Limitations

- **No historical financial statements**: yahooquery only provides current fundamentals. This makes it unsuitable for the `backtest` subcommand, which needs yearly/quarterly `income_stmt`, `balance_sheet`, and `cashflow`.
- Field availability can vary slightly between batch and single-ticker calls (e.g. `price_to_book` may be missing for some tickers in large batches).
- Yahoo may change the `all_modules` schema without notice; keep the field mapping tests green.

### Usage

```bash
investdaytip --data-source yahooquery -n 5 -r us
investdaytip advisor --data-source yahooquery
python scripts/compare_data_sources.py -t "AAPL MSFT GOOGL" -n 3
```

## FMP Data Source (`--data-source fmp`)

Financial Modeling Prep is an alternative data source selectable via `--data-source fmp`.
It uses 6 FMP endpoints per stock ticker (ETFs are **not** supported):

| Endpoint | Field | Used For |
|---|---|---|
| `profile/{ticker}` | name, sector, exchange, currency, market cap, price | ETF check, market-cap filter, basic fields |
| `ratios-ttm/{ticker}` | PE, PB, PEG, profit margin, D/E, current ratio, div yield, payout ratio, FCF/share | Valuation, quality, health, income |
| `key-metrics-ttm/{ticker}` | ROE, ROA | Quality |
| `financial-growth/{ticker}` | earnings growth, revenue growth | Quality |
| `historical-price-eod/{ticker}` | OHLCV for 2 years (split/dividend-adjusted via `adjClose`) | Trend indicators, RSI, MACD |
| `earnings-surprises/{ticker}` | EPS surprise over last 4 quarters | `quant` model EPS Revisions factor |

**Free tier limits:** 250 requests/day → ~40 tickers/day (6 calls/ticker).

### Rate-Limit Handling & Automatic Fallback

When FMP returns HTTP 429 (Too Many Requests) or a JSON `"Error Message"` containing "limit", or
when FMP is unreachable (`URLError`, `OSError`, `http.client.HTTPException`, invalid JSON):

1. **Pre-flight check** (`check_rate_limit()`) sends a single `profile/SPY` request before the batch starts. If rate-limited, all tickers go directly to the fallback.
2. **During fetch** (`recommend()` loop), per-ticker `FmpRateLimitError` is caught and that ticker is added to `leftovers`.
3. **Automatic fallback** — remaining tickers are re-fetched with yfinance (no user prompt). If the pre-flight itself raises a non-rate-limit `FmpError` (FMP down, invalid key), the entire universe falls back to yfinance.
4. A message is printed to stderr: `⚠️  FMP rate limit / unavailable — continuing with yfinance for N tickers`

### CLI & Config

- `--data-source {yfinance,yahooquery,fmp}` (default: `yfinance`)
- Requires `FMP_API_KEY` env var (startup prints install instructions if missing)
- Shows a warning banner: `FMP free tier: 250 requests/day (~40 tickers), 10s timeout per request.`
- Works with `advisor` subcommand (`investdaytip advisor --data-source fmp`)
- Supported in programmatic API: `get_recommendations(data_source="fmp")`

### Timeouts & Performance

| Setting | Value |
|---|---|
| Per-request HTTP timeout | `FMP_REQUEST_TIMEOUT = 10s` |
| Per-ticker total timeout | `FMP_TICKER_TIMEOUT = 90s` |
| Retry backoff (network errors) | `[2, 5]s` (2 retries) |
| Rate limit detection | Immediate (no retry on 429) |

### Error Handling

- Missing `FMP_API_KEY` → startup error with setup instructions, exit code 1
- HTTP 404 (bad ticker) → `FmpError`, ticker skipped
- HTTP 429 (rate limit) → `FmpRateLimitError`, yfinance fallback
- Network timeout → retry up to 2 times, then `FmpError`, ticker skipped
- FMP unavailable (network, API key invalid) → caught by pre-flight, all tickers fallback to yfinance

### Testing

- `tests/test_data_source_fmp.py` — 13+ tests with mocked `_get()`
- Tests use `_build_mock_get(responses)` to return canned data based on URL suffix
- Must mock `_get()` directly (not HTTP), since FMP uses `urllib.request.urlopen` internally
- The pre-flight check calls `_get("profile/SPY")` — the mock must include this path
- `FmpRateLimitError` tests: mock `_get` with `side_effect`, mock `fetch_asset` (yfinance) for the fallback pass

## StockFit PIT Data Source (`backtest --pit-source stockfit`)

Point-in-time fundamentals for the `backtest` subcommand, sourced from
StockFit (Second Dot LLC, `api.stockfit.io`): SEC annual filings together with
their acceptance dates (`dateFiled`), so every snapshot uses exactly the data
that was public on that date instead of a fixed reporting lag.

| Aspect | Value |
|---|---|
| Env var | `STOCKFIT_API_KEY` — only required when the flag is used (fail-fast check before any fetch) |
| CLI | `investdaytip backtest --pit-source stockfit` (choices: `none` (default), `stockfit`) |
| API param | `run_backtest(..., pit_source="stockfit")` |
| Endpoints | 3/ticker: `financials/income-statement`, `financials/balance-sheet`, `financials/cash-flow-statement` (`period=annual&limit=12`) |
| Scope | US-listed **stocks only**; per-ticker soft failure → that ticker falls back to the classic fixed-lag path; if **all** tickers fail the run aborts with an error (never silently degrades) |
| Rate limit | in-process cross-thread limiter **paced to the detected plan's budget** (official pricing 2026-10-07: Free 50, Starter/Stock/ETF 300, Professional 500 req/min; × 0.9 safety). `detect_plan()` re-paces on every call, `_ensure_plan_rate()` covers the fetch entry points, and until detection the paid default (300) applies — a hard-coded 0.13s (≈460/min) made a Starter run 429 after ~300 requests and demoted 106/197 tickers to yfinance |
| Errors | `StockfitError` / `StockfitRateLimitError` (subclass of the former); HTTP 404 and API `{"error": ...}` → empty series (soft) |

### Key behaviors

- **Entity-stitching auto-fallback** — when the ticker's resolved entity has
  <3 annual periods (holdco reorgs: XOM resolves to `ExxonMobil Holdings Corp`,
  CIK 2115436, empty history), `_search_queries()` derives up to 3 search
  strings from the profile name (full name → first word → camel-split first
  fragment: "ExxonMobil Holdings Corp" → "Exxon"), queries
  `lookup/search?includeDelisted=true`, probes ≤5 candidate CIKs
  (`type == "stock"`, current CIK excluded, deduped) and keeps the **longest**
  series (XOM → CIK 34088, 12+ FYs). Verified live: `stitched=True`.
- **Staleness guard on stitching** (`_series_is_recent()`,
  `STITCH_MAX_PERIOD_AGE_DAYS = 550`) — a candidate predecessor is skipped
  when its latest fiscal period ended >550 days ago (~18 months; the widest
  gap a live annual filer shows). Without it the "longest series" rule grafts
  a **defunct name-match** onto the live ticker: `SNDK` name-searches to the
  old SanDisk (CIK 1000180, acquired 2016, last FY2015, 12 FYs) instead of
  the current Sandisk Corp (CIK 2023554, spun off 2025, <3 FYs) → SNDK scored
  2015 fundamentals (growth −61% with the TTM overlay, +2843% without).
  `load_pit_snapshot()` applies the same guard **only to stitched snapshots**,
  so a poisoned file self-heals (refetch with a key, else fall back to
  yfinance) while a non-stitched series of a company that simply stopped
  filing stays usable. Regression tests:
  `test_entity_stitching_skips_stale_predecessor`,
  `test_stale_stitched_snapshot_is_rejected`.
- **Shares fallback** — balance sheets without `sharesOutstanding` (e.g. FLWS
  reports none) derive basic shares as `netIncome / eps` from the same filing;
  non-positive results → `None`.
- **Shared derivation path** — `_build_pit_stock_data()` and
  `_build_historical_stock_data()` both end in `_derive_stock_data()`, so a
  PIT-vs-classic comparison changes only the data source, never the math.
- **Definitional differences vs yfinance** — StockFit `totalDebt` excludes
  lease liabilities that yfinance's `TotalDebt` includes (AAPL FY2022 D/E:
  221% vs 261%); StockFit serves 12 FYs where yfinance annual statements cover
  ~5 with the oldest column often `NaN` — so PIT has real fundamentals at old
  snapshots where the classic path scores neutral.

### Validation (2026-09-25, before/after on identical configs)

Full US universe, 5y, 3-month intervals, top-5, min-cap 0:

| Metric | classic | PIT | Δ |
|---|---|---|---|
| Alpha | 6.85% | 6.54% | −0.31pp (flat) |
| Sharpe | 0.74 | **0.85** | **+0.11** |
| Max drawdown | 23.99% | **11.00%** | **−13pp** |
| Win rate 12M | 50.0% | **56.2%** | **+6.2pp** |
| Cumulative | 398.9% | 387.4% | −11.5pp |

→ AGENTS "Consider" outcome: risk-adjusted metrics improve, alpha flat →
shipped as **opt-in** (needs a StockFit key; default path byte-identical).
A 6-ticker toy subset (`AAPL MSFT GOOGL JPM XOM FLWS`) instead showed a large
regression (alpha +5.6% → −7.8%) driven by 3 FLWS picks whose *filed* FY2021
fundamentals looked excellent (PE 14.8, ROE 23%) before a −71% crash —
subset results dominated by 2-3 value picks are noise; always validate on the
full universe. In that run classic vs PIT picks were **identical for all
2024-2025 snapshots** (where yfinance data is complete) and differed only
2021-2023 (where classic fundamentals are `NaN`/None).

### Local PIT snapshot (`scripts/pit_snapshot.py`)

Lets `backtest --pit-source stockfit` run **without an API key**: annual
statements (12 FY with `dateFiled`) are fetched while a key is valid and
stored per-ticker as JSON under `~/.investdaytip/pit/` (override with
`STOCKFIT_PIT_SNAPSHOT_DIR` — tests point it at `tmp_path` via an autouse
`conftest.py` fixture, so the real home is never touched).

- **Resolution order** in `fetch_pit_statements()`: live StockFit (key set) →
  local snapshot → empty result (classic fixed-lag fallback, per ticker).
  A successful live fetch refreshes the snapshot; an empty or failed live
  fetch never wipes an existing file (atomic write via `.tmp` + `replace`).
- **Fail-fast** is `check_pit_access()` (key **or** snapshot) instead of
  `check_api_key()` — in `run_backtest()` and the CLI. Message tells the user
  to build a snapshot with the script.
- **Builder**: `python scripts/pit_snapshot.py` (full 196-ticker US stock
  universe; `-t "AAPL MSFT"`, `--force`, `--status`, `--workers`). Resume-
  friendly: already-saved tickers are skipped unless `--force`.
- Built while the Pro trial was alive (2026-09-26): **196/196 tickers**
  (1–12 FY each, 2140 fiscal years, 8.2 MB). Entity stitching (XOM, BLK) is
  resolved **at build time**, so the snapshot carries the stitched predecessor
  CIK and needs no key to read.

### StockFit `lookup/search` HTTP 400 (live-verified 2026-09-26)

The search endpoint **rejects `searchString` containing `,` `/` `&` `(` `)`**
with HTTP 400 (`.` and `-` are fine): `"BlackRock, Inc."` → 400, `"BlackRock
Inc"` → results. Two defensive fixes shipped with the snapshot block:

- `_search_queries()` strips that punctuation (and trailing `.`) before use.
- The whole stitching path is **best-effort**: `_lookup_profile()` returns `{}`
  on error, `_candidate_ciks()` skips a failing query, and a failing CIK probe
  is skipped — a rejected search can never discard a ticker's by-symbol
  series (it used to fail BLK/FERG/SUNB entirely; now BLK stitches to 12 FY).

### Testing

- `tests/test_data_source_stockfit_pit.py` — mocks `investdaytip.data_source_stockfit._get`
  (same pattern as FMP; never HTTP): parsing, happy-path fetch, entity
  stitching (exact-name and short-query variants), look-ahead gating of
  `pit_fact_asof`, shares-from-EPS, per-ticker degradation guards, CLI wiring.
- `tests/test_pit_snapshot.py` — save/load roundtrip, empty-never-written,
  corrupt/missing → `None`, fetch resolution order (no-key→snapshot without
  touching `_get`, key→snapshot refresh, live fail/empty→snapshot fallback),
  `check_pit_access`, CLI runs with snapshot-but-no-key, script `--status`.
- The XOM reorg is the regression case for stitching; keep the
  `_search_queries("ExxonMobil Holdings Corp")` assertions green.

## StockFit Live Data Source (`--data-source stockfit`)

Fourth data source for the recommend path: US stocks only, key required, gated
to **Starter+** via the `live_source` capability (~7 calls per ticker — Free's
300 req/day can't scan universes).

| Aspect | Value |
|---|---|
| Scope | US stocks only — non-US universes/tickers are excluded automatically with a warning; ETFs raise `--data-source stockfit supports stocks only` |
| Fail-fast | no key → exit 1; plan below Starter → exit 1 (CLI banners) |
| Calls/ticker | `lookup/batch` (profile + type guard) → statements via `fetch_pit_statements()` (also refreshes the PIT snapshot) → `financials/income-statement?period=ttm` (TTM overlay) → `price/history` (2y daily closes) → `earnings/dividend-history`; then yfinance enrichment when `market_cap` or `eps_surprise` is missing |
| Caching | profile + dividends + TTM facts in `{ticker}:stockfit_info` (1d); statements from the local PIT snapshot when < 7 days old; prices in the shared `{ticker}:history` (15 min — both sources serve the same adjusted closes); yfinance fundamentals/statements use their existing cache |
| Fallback | `StockfitError` per ticker → automatic full yfinance fallback (same leftovers pattern as FMP); on StockFit success, yfinance fills only missing `market_cap` and `eps_surprise` (StockFit values and all other fields remain primary); user errors (ETF, below cap) are skipped |
| Rate limit | same plan-aware pacing as the PIT client (Starter ≈270 req/min effective). A transient 429 self-heals inside `_get` — limiter penalized (×2, capped at 40 req/min) + retry at `Retry-After`/minute-boundary — instead of demoting the ticker; a sustained block (>6 429s within 60s, e.g. exhausted daily budget) raises immediately and the yfinance fallback takes over |
| Trend parity | `return_12m`, `price_vs_sma200` and the improvement flags match yfinance to 8 decimals (same adjusted price series) |

**Semantics (2026-09-27, switched to TTM after the MU P/E report):** current
fundamentals are **TTM** — flows from the last 4 reported quarters, balances
and share counts from the latest quarter — fetched in one merged `facts`
block from `financials/income-statement?period=ttm` and applied by
`_apply_ttm_overlay()`. This matches yfinance's trailing semantics: MU P/E
24.5 vs yfinance 24.45 after the switch (the previous **as-filed fiscal-year**
basis showed 142.6 because the latest 10-K EPS lagged an earnings explosion;
that old basis was characterized vs yfinance at Spearman 0.441 / top-10
overlap 6/10 — rankings were source-dependent). Levels (P/E, P/B, ROE,
margins, D/E) read TTM, but the **YoY comparisons never do**:
`earnings_growth`, `revenue_growth`, the improvement flags and
`eps_acceleration` compare the **as-filed fiscal years** (FY(n) vs FY(n−1)
vs FY(n−2)) — `_apply_ttm_overlay()` parks the displaced figure in
`*_asfiled` instead of shifting the `*_prev` chain, so both sides of every
comparison are non-overlapping 12-month spans, the same basis the backtest
builders use. (2026-09-29 fix: the previous "shift the chain" behaviour
compared a TTM window against the latest filed year — which degenerates to
**exactly 0%** whenever no quarter has been filed since the fiscal year
closed: MSFT's FY2026 was filed 29-jul-2026, TTM ≡ FY2026 →
`earnings_growth = 0.0` → Growth 18 → "growth flagged as disqualifying" →
score capped at 43.3 while yfinance scored 66.5. After the fix StockFit
reads +31.3% / +17.8% and totals 67.3 vs yfinance's 67.3.) Per-field
graceful degradation: a fact missing from the TTM block (or
an empty/failed TTM fetch — symbol first, then the PIT CIK for stitched
entities) keeps its as-filed value and its untouched comparison chain; a
`StockfitRateLimitError` on the TTM call propagates so the ticker falls back
to yfinance. No analyst estimates exist in StockFit, so its raw
`forward_pe`/`peg_ratio`/`eps_surprise` are always `None`; the recommender's
field-level yfinance enrichment fills **only** `eps_surprise` when available
(EPS Revisions no longer has to stay neutral) and `market_cap` when StockFit
cannot derive it from shares. Other StockFit fields are never overwritten by
the enrichment. `forward_pe`/`peg_ratio` remain `None`. `eps_acceleration` is
derived but was **rejected** as the EPS-Revisions fallback (factor-IC −0.059) —
diagnostic only. Details: `stockfit/archivo/data_source_stockfit_validation.md`.

Robustness fixes found during that validation (keep them):
- shares preference `currentSharesOutstanding → sharesOutstanding →
  sharesIssued` (CVX/JNJ tag the first one — without the fallback P/B and
  market cap went `None`);
- dividends are summed from the latest **four quarterly** payments (TTM) —
  the annual rows sometimes report a single quarter's rate as "annual" (UNH:
  2.20 vs a real ~8.8/yr); the annual scan is only a fallback for issuers
  whose rows are empty shells (JNJ);
- **mis-scaled-facts guards** (ADR share classes / issued-vs-outstanding /
  currency mixes — TSM served a 5× market cap with P/E 225, PG 1.7×): a)
  `fetch_asset_stockfit` raises when `NI/EPS` implies shares >25% off the
  tagged count (catches PG), and b) the recommender's enrichment applies a
  plausibility screen (P/E > 150 or P/B > 60) and, for those only, a
  yfinance market-cap cross-check — >25% off replaces the whole ticker with
  the yfinance data. Both paths feed the automatic yfinance fallback.
  Clean tickers pay zero extra calls (AAPL's real P/B ~45 and NVDA's ~50 do
  not trip the screen);
- ticker case is normalized at the StockFit layer (`_norm_ticker()`,
  `meta` → `META`) in every public entry point — `lookup/batch` keys its
  response by the uppercase symbol and cache keys / snapshot filenames are
  case-sensitive, so a lowercase `-t meta` used to miss all of them and
  silently fall back to yfinance (rankings are source-dependent!). The
  `_lookup_profile` key match is case-insensitive as defense in depth.

**Cross-source unification (2026-10-02):** `_fetch_stock()` (yfinance path)
derives `earnings_growth`, `revenue_growth`, `return_on_equity`,
`return_on_assets`, `profit_margin`, `debt_to_equity`, `current_ratio` and
`eps_acceleration` from the statement frames with the same formulas as
`_derive_stock_data`, and computes `free_cashflow` as
`operatingCashflow − |capitalExpenditures|` from info — overriding the Yahoo
fields, which used different definitions (quarterly-YoY growth, own ratio
bases, near-quarterly FCF values like MSFT 16.5B vs the coherent 67B TTM).
Info values survive when statements are missing. Measured on the top-20 US
(both sources through `recommend()`): Spearman 0.441 → **0.946**, top-10
overlap 6/10 → **9/10**, `earnings_growth` diffs 19/20 → **0/20**.

**TTM level basis (2026-10-02, same day):** `_fetch_stock()` now derives the
*levels* from **quarterly** frames via `financial_health.ttm_facts()` (flows
= sum of the last four quarters, balances = latest quarter — identical to
StockFit's `period=ttm` overlay); comparisons keep their FY-vs-FY basis.
FCF prefers the quarterly cash-flow sum. Measured on the same top-20:
ROE diffs 13/20 → **3/20**, `current_ratio` 7/20 → **0/20**, FCF 16/20 →
**4/20**, Spearman 0.946 → **0.971**. Residuals are *definitions*, not bases:
`debt_to_equity` (StockFit `totalDebt` vs yfinance `Total Debt`, 11/20) and
AMZN capex treatment. Details: `stockfit/archivo/top20_comparison.md`.

GICS sector names are mapped to yfinance-style (`Information Technology` →
`Technology`) so `-s` filters and the advisor sector tilt behave identically.
Tests: `tests/test_data_source_stockfit_live.py` (fetcher, guards, gates,
US-only filtering, yfinance fallback) and `tests/test_stockfit_rate_limit.py`
(plan-aware pacing, 429 retry/penalization/circuit breaker).

## StockFit Fundamental Insights (`--fundamental-insights`)

Opt-in section in the recommendations HTML report with SEC-derived fundamentals
for the displayed US stocks. Flag **off by default → output byte-identical to a run
without it**; without `STOCKFIT_API_KEY` it prints a warning and omits the section
(graceful degradation — same posture as `--superinvestor`).

- **3 Free-tier chart endpoints** per ticker (annual, 5 fiscal years):
  `financials/chart/revenue-profitability` (Gross/Operating/Net margin + revenue),
  `earnings/chart/quality` (FCF/NI, OCF/NI), `financials/chart/balance-sheet-health`
  (Debt/Equity, Current Ratio). Response shapes verified live 2026-09-26.
- **Scope**: only `asset_type == "STOCK"` with `infer_region_from_ticker() == "us"` —
  ETFs and EU/Asia tickers never call StockFit (0 wasted API calls).
- **Render** (`html_export._render_insights_section`): summary table (latest FY +
  trend arrows ↗/→/↘ colored by outcome — D/E falling is green, FCF/NI falling red)
  plus one `<details>` block per ticker with the full fiscal-year grid. Metrics
  are server-rendered; each summary row and detail block carries a
  case-insensitive ticker key so the report's shared JS filters (search, asset
  class, region, minimum score/returns) show and hide matching insights with
  the recommendation rows. The section hides when no insight ticker matches.
- **Fetch** (`main._fetch_report_insights`): `ThreadPoolExecutor(5)`, soft-fail per
  ticker, never raises. `fetch_fundamental_insights()` (in `data_source_stockfit.py`)
  never raises either — a failed endpoint degrades to partial data, all-fail → `None`.
- **Cache**: `{ticker}:stockfit_insights`, TTL 1d — written only when all 3 endpoints
  succeeded (partial results are refetched on the next run).
- Orthogonal to `--data-source`: prices/fundamentals stay on yfinance/yahooquery/FMP,
  insights come from StockFit.
- Post-trial: these 3 calls are in the Free tier (34 endpoints, 300 req/day), so the
  section keeps working at $0 — only `financials/scores` (Piotroski/Altman) or the
  Pro-only `footnotes/*` risk endpoints would need a paid tier later.

```bash
investdaytip -t "AAPL MSFT" -n 5 --export-html report.html --fundamental-insights
```

### Testing

- `tests/test_fundamental_insights.py` — mocks `_get` (never HTTP): chart merge
  parsing, partial/total endpoint degradation, missing key (real `_get` raises before
  I/O), cache roundtrip via `enabled_temp_cache`, HTML render (section presence,
  trend arrows, negative-red), CLI wiring (flag on/off/without `--export-html`), and
  `_fetch_report_insights` filtering (US-stocks-only, per-ticker failures).

## Deep-dive Subcommand (`investdaytip deep-dive`)

Product Fase 2: per-ticker report combining three sources, rendered to the
terminal (Rich) and optionally to a self-contained HTML page.

```bash
investdaytip deep-dive -t AAPL
investdaytip deep-dive -t "AAPL MSFT" --export-html report.html
```

| Section | Source | Needs key? |
|---|---|---|
| InvestDayTip score + factor breakdown | live `fetch_asset` + `score_stock` | no |
| Earnings snapshot (EPS, margins, ROE/ROIC, FCF, growth, next dates) | StockFit `company/research-summary` (Starter tier) | yes — omitted with a note otherwise |
| Piotroski F-Score (9 checks ✓/✗) + Altman Z + zone | **local** (`financial_health`) from the ticker's own annual statements; Altman alone falls back to StockFit's snapshot `altmanZScore`/`altmanZone` when a statement row is missing (never both fields → `None`) | no |
| Devil's advocate — risk signals (local + footnotes layer) | `risk_signals`: keyless heuristics + StockFit `footnotes/*` bullets (Pro; segmentation pair on Starter) | no — footnotes omitted with a note below Pro |

- Piotroski/Altman are **diagnostics, never scored** (validated and rejected
  as scoring factors); both renders label them as such.
- **Altman Z convention (verified vs StockFit 2026-10-06)**: X4 uses the
  **book value of equity attributable to the parent** (`StockholdersEquity`,
  excluding minority interests) over total liabilities — StockFit's documented
  proxy in `financials/scores` — **not** the 1968 paper's market-value term.
  This keeps the keyless local diagnostic numerically identical to StockFit's
  precomputed `altmanZScore` (reproduced exactly on AAPL 2.42, MSFT 2.64,
  TSLA 2.40, INTC 1.52, T 0.87 and AMT 0.24 — AMT discriminates parent vs
  total equity). `altman_z_score(cur)` takes no market cap.
- **Devil's advocate block** (`risk_signals.risk_signals()` + `footnote_risk_signals()`)
  — two layers merged into one severity-sorted list, each bullet tagged with
  its source. **Layer 1 (keyless local)**: Altman zone (distress→high,
  grey→medium), losses, negative FCF, D/E > 200%, payout > 100% / > 85%,
  failed Piotroski checks (accruals → medium, dilution & rising leverage →
  info, both margin+ROA deteriorating → medium). **Layer 2 (StockFit
  `footnotes/*`, 2026-10-06)**: `deep_dive.build_deep_dive()` fetches the
  footnotes the plan unlocks via `fetch_footnotes()` (Pro: concentration,
  debt-structure, credit-facilities, stock-compensation, retirement-plans,
  supplier-finance, fair-value-hierarchy; Starter: revenue/business
  segmentation) and `footnote_risk_signals()` derives bullets — key-customer
  / supplier concentration, debt maturity wall (≥25% of face due ≤2y),
  credit-line utilization (≥50/75%), unrecognized SBC vs market cap,
  underfunded pensions, supplier-finance obligations, Level 3 share,
  geographic/product/segment revenue concentration. Below Pro the report
  shows `Pro footnotes omitted — requires Pro plan (current plan: …)`; below
  Starter / without a key the whole layer degrades to a note. Context only —
  never scored.
- **Footnote parsing is defensive by design** — the Pro `footnotes/*` response
  shapes come from the API **documentation only** (the session's plan is
  Starter, so they could not be verified live; the segmentation pair was
  verified 2026-10-06: `geography.{countries,usStates,regions,residuals}[]` /
  `product[].{name,value}` and `segments[].{role,metrics.revenue}`). **All
  four geography buckets feed the total** — countries *or* regions alone is
  how GOOGL read "100% of revenue from United States" at 48% actual (0.17.2);
  a region contributes only its `other`/unexplained remainder so a rollup
  never double counts, and `usStates` are US-internal splits of the US leaf.
  The **Geographic** bullet then needs the map to *reconcile with the filer's
  revenue* (0.90–1.10× of `annual_facts()["TotalRevenue"]`, passed as
  `revenue=` from `build_deep_dive()`): partial maps (NFLX/CSCO/EQIX/EMR, all
  0.39–0.54×) and double-counted ones (JNJ, 1.43×) yield silence rather than a
  share, and **Product** lines above that revenue are dropped — the denominator
  *is* the revenue (0.17.3).
  Every
  reader looks up documented field names (`share`, `dueYear`, `faceAmount`,
  `utilization`, `unrecognized*Cost`, `fundedStatus`, `*outstanding*`/
  `*obligation*`, `level3Share`) and emits **silence** on an unknown shape —
  never a fabricated bullet. **When a Pro key is available, verify the shapes
  live and tighten the parsers** (`tests/test_footnotes.py` pins the
  documented shapes).
- **Unit convention (verified live 2026-09-27)**: StockFit snapshot
  margins/returns (`grossMargin`, `operatingMargin`, `netMargin`, `roe`,
  `roic`, `fcfToNetIncome`) are **percent-form** (40.31 = 40.31%) while
  `revenueGrowth`/`epsGrowth` are decimals — `deep_dive._norm_snap()`
  converts percent-form to decimals once so both renderers share one
  formatting path.
- **DCF link-out intentionally NOT rendered** (user decision 2026-09-27):
  StockFit's valuation platform is in **early access** (only the landing page
  is public, no per-ticker routes) and advertising a DCF users cannot open
  would be misleading. `deep_dive.DCF_URL` is reserved — re-enable the
  link-out (as a per-ticker deep link) only when the app actually works.
- Statements come via `data_source.fetch_statement_frames()` (income +
  balance, 7d cache) and `data_source.fetch_cash_flow_frame()` (needed for
  Piotroski's OCF check). Stocks only — an ETF gets a "stocks only" error
  note in its report.
- Cache: `{ticker}:stockfit_research`, TTL 1d (written on success).
- Tests: `tests/test_deep_dive.py` — research fetch/parse/cache, build with
  and without key, render smoke (incl. unit normalization), CLI wiring.
  Mock `investdaytip.deep_dive.fetch_asset` / `fetch_statement_frames` /
  `fetch_cash_flow_frame` (never yfinance).

## Tier-aware degradation (product principle)

**InvestDayTip works on every StockFit tier — and without a key at all.**
Features unlock more with a higher tier (or any key) and degrade gracefully
below it: they are omitted with an explicit reason, never fabricated.

- `data_source_stockfit.CAPABILITIES` maps each capability to its minimum
  plan (`None` = works keyless): `pit_statements` keyless (local snapshot),
  `fundamental_insights` free, `deep_dive_summary` starter,
  `economic_model` stock, `footnotes` pro, `footnotes_segments` starter,
  `live_source` starter
  (`--data-source stockfit`).
- `detect_plan()` — StockFit exposes no plan endpoint, but gated endpoints
  answer **HTTP 403** ("Feature not available on current plan"), so the plan
  is detected by probing the tier boundaries top-down
  (`footnotes/concentration` → pro, `company/economic-model` → stock,
  `financials/scores` → starter, else free). Returns `none` without a key and
  `unknown` on network errors — **`unknown` never blocks a feature** (try and
  degrade at the fetch). Cached in-process + `_global:stockfit_plan` (1d).
- `StockfitPlanError(StockfitError)` is raised by `_get` on HTTP 403 (no retry).
- `plan_allows(plan, capability)` is the single gate used by features (e.g.
  `deep_dive` skips the research-summary fetch below Starter and says
  "requires Starter plan (current plan: free)").
- `investdaytip stockfit-status` prints the detected plan + capability matrix
  so any user can see what their scenario unlocks.
- Tests: `tests/test_stockfit_plan.py` (detection per tier, matrix incl.
  keyless/unknown, gated skip in deep-dive). `conftest.py` strips any ambient
  `STOCKFIT_API_KEY` so tests can never hit the live API.

## Conventions & Gotchas

- **Nothing may print/log at WARNING+ during `recommend()`'s Rich progress bar** — stderr writes interleave with Live's in-place redraw and fragment the bar (seen: 11+ fallback warnings shattering it). Per-ticker chatter goes to `logger.info`; aggregated notices are collected via `recommender.take_fallback_notices()` and printed by `main` once the bar is done.

- `from __future__ import annotations` in every annotated module (not in `__init__.py` or universe files)
- `Optional[float]` for dataclass fields; `Iterable[str] | None` for function params with `from __future__ import annotations`
- `field(default_factory=list/dict)` for mutable defaults on dataclasses
- `_safe_get(info, key)` — extracts/validates `Optional[float]` from yfinance info dicts; rejects non-finite values (NaN **and** ±inf) via `math.isfinite`
- `_first(*values)` — returns the first non-`None` value; used for fallback chains so a legitimate `0.0` is preserved (an `or` chain would discard it)
- `_sanitize_yield(value)` — normalizes yfinance yield fields to decimals; values greater than `1.0` are divided by 100 because some tickers (notably European ones) report `dividendYield` as an already-multiplied percentage (e.g. `4.05`) while most US tickers report decimals (e.g. `0.0405`)
- `_ttm_dividend_yield(dividends, price)` — computes a stock's trailing-twelve-month dividend yield from `Ticker.dividends` (summed over the last 365 days) divided by the current price; used as the primary yield source because yfinance's `dividendYield` can be wrong by 100x for tickers like AAPL and V. Falls back to `_sanitize_yield(info["dividendYield"])` when raw dividends are unavailable
- `_technical_indicators(close, high, low)` — returns `(rsi_14, macd_histogram, ema_cross, adx_14, stochastic_k)`. EMA cross is `(EMA50 - EMA200) / close` (positive = bullish). ADX uses Wilder's smoothing (14-period). All 5 indicators contribute to `_technical_score()`: RSI 20%, MACD 30%, EMA cross 25%, ADX 15%, Stochastic %K 10%
- `_suppress_stderr()` context manager wraps **every** yfinance call — without it, yfinance spams stderr
- Asset type dispatch: `score_asset()` → `score_stock()` / `score_etf()` via `isinstance`; `model` param controls ETF scoring too (`quant` → `score_etf_quant()`, `classic` → `score_etf()`)
- `ScoredAsset` unified output; `ScoredStock = ScoredAsset` backwards-compatible alias
- Universe export naming: US + EU use `DEFAULT_` prefix (`DEFAULT_UNIVERSE`, `DEFAULT_EU_ETF_UNIVERSE`), Asia does not (`ASIA_UNIVERSE`, `ASIA_ETF_UNIVERSE`), Superinvestor uses `SUPERINVESTOR_UNIVERSE`
- `_build_universe()` accepts `currency` param; when `currency != "all"` and `region == "all"`, it derives region from currency (USD→us, EUR→eu, JPY→asia) to reduce API calls. It deduplicates the merged pools case-insensitively (overlapping universes share tickers like `VXUS`/`IEMG`)
- `_TICKER_ALIASES` in `recommender.py` maps a secondary listing to its canonical symbol (`2330.TW→TSM`, `9988.HK→BABA`, `RACE.MI→RACE`, `RIO.AX→RIO.L`, `ASML.AS→ASML`, `0005.HK→HSBA.L`). It is applied **only** when more than one pool is merged (`len(pools) > 1`), so a single-region scan keeps its local listing
- Superinvestor region is stocks-only (no ETF universe); tickers are US-listed with direct 13F data from DataRoma

### Caching
- `CacheDB` in `cache.py`: SQLite with `threading.local()` per-thread connections, WAL mode, write lock via `threading.Lock`
- Thirteen cache entry types:
  - `{ticker}:info` (fundamentals, TTL 1d — flat yfinance-style dict)
  - `{ticker}:fmp_info` (FMP `{"profile", "ratios_ttm"}` schema, TTL 1d) — separate key so FMP's incompatible schema never poisons the shared yfinance-style `info` entry (and vice versa)
  - `{ticker}:history` (prices, TTL 15min)
  - `{ticker}:balance_sheet` / `{ticker}:income_stmt` / `{ticker}:cash_flow` (financials, TTL 7d)
  - `{ticker}:dividends` (dividends, TTL 7d)
  - `_global:fear_greed` (CNN Fear & Greed Index, TTL 1h)
  - `superinvestor:holdings` (DataRoma aggregated data, TTL 7 days)
  - `{ticker}:stockfit_insights` (StockFit insight charts, TTL 1d — written only on a complete 3-endpoint fetch)
  - `{ticker}:stockfit_research` (StockFit research summary for `deep-dive`, TTL 1d — written on success)
  - `{ticker}:stockfit_footnotes` (StockFit `footnotes/*` payloads for the deep-dive bear case, TTL 1d — written only on a complete fetch of every endpoint the plan unlocks)
  - `{ticker}:stockfit_info` (StockFit profile + latest DPS + TTM facts for `--data-source stockfit`, TTL 1d)
  - `_global:stockfit_plan` (detected StockFit plan for tier-aware gating, TTL 1d — never stores `unknown`)
- `fetch_asset()` defers cache-write until both info and history are fetched (atomic snapshot); partial results cached on history failure
- Backtest **disables cache entirely** to ensure reproducible results — stale history cache can shift `_latest_common_end()` and produce different snapshot counts; cache state is saved and restored via `try/finally`
- Connections are tracked so `CacheDB.close_all()` / module-level `close_db()` can release **worker-thread** connections; `recommend()` calls `close_db()` in a `finally` after the pool tears down
- `--no-cache` flag disables cache read/write; `--cache-clear` drops all entries. Both flags work on `backtest` subcommand too
- `--superinvestor` flag enables the DataRoma superinvestor cache warm-up (~80 HTTP requests) and the "Superinvestors" column in both HTML and CLI output; disabled by default
- Tests auto-disable cache via `conftest.py::disable_cache` (autouse); an autouse `no_network` guard fails fast on unmocked `yf.Ticker`; `enabled_temp_cache` fixture backs cache tests with a `tmp_path` DB (never the real `~/.investdaytip`)
- **Quote and history can sit a session apart** (`{ticker}:info` 1d vs `{ticker}:history` 15min — different Yahoo endpoints): `fetch_asset()` refetches history once past the cache when `_history_lags_quote()` sees the chart's last session behind `regularMarketTime`, and `_apply_history_common()` always sets `current_price` from history's last close, which is the base of every trend/RSI metric (`info`'s price fills in only when there is no history at all). Never let one row mix the two — the 2026-10-06 site run shipped 18 of the top-100 with an Oct-6 price next to Oct-5 1M/daily-change/RSI (FIVE: 209.92 vs −10.01%) When the chart is **still** behind after that refetch the provider itself is missing the bar (Yahoo's nightly rebuild: at 2026-10-08 01:16 UTC all 100 tickers lacked the Oct-7 bar while `info` already carried its close), so `_append_quote_session()` writes the quote's close as the series' last bar — close only, never an interpolated high/low/volume, and never cached, so the provider's own bar replaces it — and a row must not report a session older than the quote closed and `_drop_partial_session()` removes the bar of a session still in progress (16:00 New York is what makes a daily bar final) — a mid-day run then describes the previous session, which is the date the site files it under

### Rate Limits & Error Handling
- `fetch_asset()` retries on `YFRateLimitError` with delays [10, 30, 60]s then returns error dataclass
- Missing data → neutral 50 (never crashes); yfinance errors caught per-ticker, stored in `errors` list
- FMP rate limit (429 or JSON "limit") → `FmpRateLimitError` → auto-fallback to yfinance

### CLI Quirks
- `--export-html` uses `nargs="?"` with `const=""` — no arg means auto-generated filename `investDayTip[-<tag>]-yyyymmdd-hhmm.html`; tag derived from tickers-file stem (stopwords filtered)
- `advisor` subcommand duplicates many flags from main parser but some default to `None` for interactive prompts
- Ticker files: supports newlines, spaces, commas, and `#` comments; `_merge_ticker_lists()` deduplicates case-insensitively preserving first-occurrence casing
- `-t` quoted strings are split on whitespace (e.g. `-t "AAPL MSFT"` works); `-n` defaults to ticker count when `-t` is used
- `--min-market-cap` filter: defaults to `0` when explicit tickers are passed (`-t`/`--tickers-file`), `2B` otherwise (e.g. `1B`, `500M`, `0` to disable); applied against native-currency figures, approximate for non-USD. When the filter is active, assets with **missing** market cap are excluded (a missing figure can't satisfy the filter), and history fetch is skipped for them
- Currency filter keeps assets whose `currency` is `None` (a missing field shouldn't silently drop an otherwise-valid candidate)
- `-r`/`--region` and `-c`/`--currency` use `nargs="+"` — pass multiple values: `-r us eu`, `-c USD EUR`. Both `str` and `list[str]` accepted programmatically.
- `-s`/`--sector` accepts a single string; prefix match case-insensitive (e.g. `Financial` matches Financial Services). Applied inside `recommend()` before the `top_n` truncation, so it filters the full universe rather than just the top results.
- `--superinvestor` controls DataRoma scraping (~80 HTTP requests) and the "Superinvestors" column; the curated `SUPERINVESTOR_UNIVERSE` tickers are **always** included in the stock pool (they are quality stocks), but the manager-count data and column are only fetched/displayed when the flag is present
- **Tab completion**: `argcomplete>=3.0` is a dependency; `argcomplete.autocomplete(parser)` is called before `parse_args()` so `investdaytip -<TAB>` and `investdaytip --region <TAB>` work after running `eval "$(register-python-argcomplete investdaytip)"`

### Backtest Module
- Stocks only (no ETF support); banner says `(stocks only)` at runtime
- Uses **annual fiscal-year** financial data via `t.income_stmt`, `t.balance_sheet`, `t.cashflow` (yearly properties) — quarterly data (`get_income_stmt(freq="quarterly")`) only returns 5 quarters which is insufficient
- `--pit-source stockfit` (default `none`) swaps fundamentals for StockFit point-in-time filings — exact `dateFiled` knowledge dates instead of the fixed `--lag-days` assumption; see the StockFit section. Per-ticker degradation to classic; aborts if every ticker fails.
- Classic and PIT builders share `_derive_stock_data()` — the math is identical, only the input selection differs
- Internal dict key is `cash_flow` (underscore), matching the cache key; yfinance property is `t.cashflow` (no underscore) — cache read uses `"cash_flow"` to match the write
- `--cache-clear` and `--no-cache` flags are supported on the backtest subcommand (cache is disabled by default during backtest for reproducibility)
- Progress bar uses `TimeElapsedColumn` (not default `TimeRemainingColumn`) so elapsed time counts up and never resets to 0

### Advisor Module
- `market_regime()` fetches `^VIX` and `^VXN` via yfinance; thresholds: ≤15 bullish, ≤25 neutral, ≤35 bearish, >35 crash
- `bubble_risk()` computes VIX percentile over trailing 2 years; <15% → high (complacency), 15-30% → medium, >30% → low
- `macro_regime()` — composite 0-100 macro health score combining VIX + 10Y-2Y yield curve + MOVE index (bond vol) + DXY (dollar strength) + CNN Fear & Greed; regimes: ≥70 healthy→buy, ≥45 neutral→hold, ≥25 warning→hold, <25 danger→sell
- `portfolio_review()` loads tickers from file with `_load_tickers_from_file()`, passes through `recommend()` scoring, returns categorized results
- `run_comprehensive()` is the programmatic API for multi-region/asset-class analysis; exports HTML per combination to `advisor_recommendations/`
- `advisor_main()` writes the final HTML report to `advisor_recommendations/recommendations_advisor_<timestamp>.html`

### ETF Data Specifics
- Expense ratio has 3 fallback sources: `annualReportExpenseRatio` → `netExpenseRatio` → `funds_data.fund_overview`
- yfinance returns expense ratios as percentage values (e.g. `0.03` = 0.03%); thresholds in scoring use this format directly
- Sharpe proxy: `(return_12m - RISK_FREE_RATE) / volatility_1y` where `RISK_FREE_RATE = 0.045`
- `EtfData.sector` returns `category`; `EtfData.market_cap` returns `total_assets` (AUM)
- `EtfData` stores `return_3m` and `return_6m` (computed from price history in `_apply_history_common`) for the quant model's momentum factor

### URL Building (html_export)
- `_normalize_exchange_hint()` maps yfinance exchange codes: NMS/NGM/NCM→NASDAQ, NYQ→NYSE, ASE/PCX→NYSEARCA
- `_exchange_mapping()` covers all suffix→exchange pairs (DE→ETR/XETR, PA→EPA/EURONEXT, L→LON/LSE, etc.)
- `infer_region_from_ticker()` suffix sets must stay in sync with `_exchange_mapping`: EU includes `F` (Frankfurt); Asia includes `SS`/`SZ` (Shanghai/Shenzhen)
- `HWM` override → NYSE (unsuffixed but NYSE-listed)
- Unmapped suffixes → fallback Google Finance search URL
- Server-rendered rows and client-side JS share NaN semantics: `_is_finite_number()`/`_pct_class()` mirror the JS `pctClass()` (None/NaN → muted, never red)

### DataRoma / Superinvestor Specifics
- DataRoma data is quarterly (45-day lag), US-only (13F), no official API → scraping-based with caching
- Control via `--superinvestor` CLI flag: disabled by default, avoids ~80 HTTP requests when not needed
- `fetch_superinvestor_universe()` warms the cache via ~80 HTTP requests (one per manager); shown with a Rich progress bar
- `score_stock()` looks up `get_superinvestor_data()` to populate `superinvestor_count` on `ScoredAsset`
- HTML export renders a sortable "Superinvestors" column between 1Y and Score only when `--superinvestor` is active; `-` when the ticker is not in the DataRoma universe
- CLI (Rich table) also shows a "Sup." column only when `--superinvestor` is active
- `SUPERINVESTOR_UNIVERSE` tickers are **always** included in the stock pool (quality stocks); the DataRoma manager-count and column are controlled by `--superinvestor`
- No ETF universe exists for superinvestor

### Scoring Models

Two stock-scoring models are available, selectable via `--scoring-model {classic,quant}` on the CLI and `scoring_model=` in the API. `quant` is the default.

- **`quant`** (default) — Seeking-Alpha-inspired five-factor model:
  - Value 25%, Growth 20%, Profitability 25%, Momentum 15%, EPS Revisions 15%
  - Value's PEG sub-metric is **derived** (`trailing_pe / (earnings_growth * 100)` — growth as a **percentage**, the classic PEG convention the scorer's `best=0.8/worst=3.0` thresholds assume; positive growth only — else `None` → neutral; applied in `_derive_stock_data` and `_fetch_stock`, so backtest + both live sources share it). **Gotcha:** dividing by the decimal growth (pre-0.15.2) inflated every PEG 100×, zeroing the sub-metric and tripping the disqualification cap — LLY/AVGO scored exactly 50.0 in the advisor; IC is Spearman so the unit scale never showed it, only the absolute sub-scores did. Factor-IC 2026-10-02: `peg_derived` +0.055 mean / 86% hit vs P/E −0.029 and P/B −0.011 — the best of the Value family. It also unifies the sources (yfinance's `pegRatio` is analyst-based and often absurd: BMY 17.37 with +178% growth). Validated before/after (full US, top-5, min-cap 0): 5y alpha 5.75%→6.51%, Sharpe 0.68→0.73; 3y alpha 12.61%→14.54%, Sharpe 1.56→1.84, win12M 75%→100% — never worse on any metric. Baselines: `stockfit/archivo/baseline-{before,after}-peg{5y,3y}.json`.
  - Profitability sub-weights: ROE 30%, margin 25%, ROA 15%, **YoY-improvement 30%** (Δgross margin + ΔROA vs previous fiscal year; neutral 50 when unknown). Shared logic in `financial_health.improvement_flags()`; flags live on `StockData.margin_improving`/`roa_improving`, filled by the live path (`fetch_asset(with_improvements=True)` — 2 statement calls, 7d cache; skipped for `classic`) and by both backtest builders. Validated 2026-09-27 (full US, top-5, min-cap 0): factor-IC +0.134/+0.126 mean (86% hit — top of the table), before/after 5y alpha 6.85%→7.60% (MaxDD unchanged), 3y alpha 7.69%→13.37%, Sharpe 1.16→1.30, win12M 50%→62.5%.
  - **Piotroski F-Score composite and Altman Z were tested and REJECTED as scoring factors** (mean IC +0.039/+0.014 = noise; 5 of the 9 Piotroski checks are negative on large-caps). `financial_health.piotroski_f_score()` / `altman_z_score()` stay available for display use — **reserved as per-ticker diagnostic content for the future `deep-dive` subcommand (product Fase 2, see `stockfit/STOCKFIT_EVALUACION.md` roadmap)**: informative only, never scored.
  - Momentum uses **12-1 momentum** (12m return excluding the most recent month, derived from `return_12m`/`return_1m`; falls back to raw 12m when `return_1m` is missing) — the last month is short-term reversal, not momentum. Validated via factor-IC + before/after backtests (2026-07-17): alpha +0.5pp and Sharpe +0.03 on the 5y wide universe, never worse on 3y/standard configs.
  - EPS Revisions uses the average EPS surprise (Reported EPS vs Estimate) over the last four reported quarters; `lxml` is required for yfinance to expose this data. **Removal TESTED and REJECTED (2026-09-27):** the factor is IC-noise (`eps_surprise` IC −0.002, `F_eps_revisions` −0.010) but removing it and redistributing its 15% to Growth/Profitability **degraded both windows badly** (5y alpha 7.60%→4.16%, Sharpe 0.73→0.66, MaxDD 24%→27%; 3y alpha 13.37%→3.38%, Sharpe 1.30→0.92) — a near-zero-IC factor still acts as **diversifying ballast** against factor concentration. The before/after backtest overrules the univariate IC, exactly as AGENTS warns. Baselines: `stockfit/archivo/baseline-{before,after}-eps{5y,3y}.json`. A variant redistribution (e.g. to Value/Momentum rather than Growth/Profitability) would be a NEW experiment with its own pair of runs. `eps_acceleration` (as-filed EPS 2nd derivative) was separately tested as an estimates-free fallback and rejected too (IC −0.059); it stays as a derived diagnostic on `StockData`, never scored.
  - Disqualifying grades cap the total score at neutral when a factor falls into red-flag territory
  - Uses absolute thresholds (peer-relative scoring is left for a future iteration)
- **`classic`** — Original InvestDayTip model (Graham/Buffett + momentum):
  - Quality 35%, Value 25%, Health 20%, Trend 20%
  - Five technical indicators (RSI-14, MACD histogram, EMA 50/200 cross, ADX-14, Stochastic %K) blended into Trend (45% sub-weight) via `--include-technical`; opt-in for `classic`
  - `resolve_include_technical(include_technical, scoring_model)` centralizes the default: `None` → `True` for `quant`, `False` for `classic`

Two ETF-scoring models are also available, controlled by the same `--scoring-model` flag:

- **`quant`** (default for ETFs when `--scoring-model quant`) — Momentum-first five-factor model:
  - Momentum 65% — 1M(15%), 3M(25%), 6M(30%), 12M(30%) returns
  - Risk 15% — volatility + beta (lower is better)
  - Cost 12% — expense ratio only (lower is better)
  - Liquidity 8% — log-scale AUM
  - Disqualifying grades cap the total when risk or cost is extreme
  - `return_3m` and `return_6m` are computed from price history in `_apply_history_common()`
  - Validated against Seeking Alpha Quant Ratings: **r = +0.645**, Top-5 alpha +16.1% vs SPY (1Y)

- **`classic`** (used when `--scoring-model classic`) — Original model:
  - Returns 40% — 3Y(35%), 5Y(40%), 12M(25%)
  - RiskAdj 25% — Sharpe ratio + volatility
  - Size 15% — log-scale AUM
  - Cost/Yield 20% — expense ratio (65%) + dividend yield (35%)

CLI examples:

```bash
investdaytip -n 5 -r us                          # quant (default)
investdaytip -n 5 -r us --scoring-model classic  # classic
investdaytip backtest -n 5 -r us --scoring-model classic
investdaytip advisor --scoring-model classic
investdaytip --data-source fmp -n 5 -r us          # FMP data source
investdaytip advisor --data-source fmp             # FMP in advisor mode
investdaytip -a etfs -n 5 -r us                    # ETF quant (default)
investdaytip -a etfs -n 5 -r us --scoring-model classic  # ETF classic
investdaytip -a etfs -c EUR                       # Euro-currency ETFs (quant)
```

Programmatic API:

```python
from investdaytip import get_recommendations
get_recommendations(top_n=5, region="us")                         # quant (default)
get_recommendations(top_n=5, region="us", scoring_model="classic")  # classic
get_recommendations(top_n=5, region="us", asset_class="etfs")       # ETF quant (default)
get_recommendations(top_n=5, region="us", asset_class="etfs", scoring_model="classic")  # ETF classic
```

When validating a new or changed scoring model, run backtest baselines for **both** models on the same universe and compare:

```bash
python scripts/scoring_baseline.py run --tag classic -r us -n 5 \
  -t "AAPL MSFT GOOGL" --period 5y --interval-months 3 --min-market-cap 0

python scripts/scoring_baseline.py run --tag quant -r us -n 5 \
  -t "AAPL MSFT GOOGL" --period 5y --interval-months 3 --min-market-cap 0 \
  --scoring-model quant

python scripts/scoring_baseline.py compare baseline-classic.json baseline-quant.json
```

## OpenCode Agent

The `advisor` subagent is configured in `.opencode/agents/advisor.md`. It defines:

- **Permissions:** bash/read allowed, write with confirmation
- **Required flow:** always ask the user before running any analysis
- **Execution methods:** `macro_regime()` (VIX + yield curve + bond vol + DXY + Fear & Greed) for full macro pulse (returns `action`: buy/hold/sell), `market_regime()` + `bubble_risk()` for quick VIX-only pulse, `run_comprehensive()` for multi-region, or interactive CLI `investdaytip advisor`
- **Devil's advocate (Fase 3 path B):** every portfolio review / buy recommendation includes a bear case — Layer 1 via `investdaytip deep-dive` (keyless risk signals, Piotroski/Altman), Layer 2 via the StockFit footnotes (`footnotes_concentration`, `footnotes_debt_structure`, `footnotes_stock_compensation`, …): the deep-dive renders them itself when the plan unlocks them, and the MCP tools (`tools.stockfit.*`, plus `insider_transactions_summary`, `executives_governance`, …) enrich the case when the server is authenticated. Never fabricate; balanced view; risk is context, never a score.
- **Output format:** clean markdown (never raw Rich tables)
- **Interpretation rules:** VIX thresholds (≤15 bullish, ≤25 neutral, ≤35 bearish, >35 crash), bubble risk (VIX percentile <15% → complacency), macro regime (composite 0-100: ≥70 healthy→BUY, ≥45 neutral→HOLD, ≥25 warning→HOLD, <25 danger→SELL), scores, portfolio signals

## Testing Notes
- Construct `StockData` / `EtfData` directly — never call yfinance in tests
- Use `tmp_path` fixture for HTML export and ticker-file tests
- Mock `investdaytip.advisor.yf.Ticker` / `investdaytip.advisor._fetch_index` for advisor tests; mock `investdaytip.recommender.fetch_asset` (and `close_db`) for recommender tests
- `tests/test_universes.py` enforces ticker-format/no-duplicate integrity across all 7 universe modules

## Backtest-Driven Scoring Validation

Every scoring change must be validated with a **before/after backtest comparison**
on the same ticker universe and configuration.  This keeps improvements objective.

### Baseline example

Config: `AAPL MSFT GOOGL`, top 2, US, 5y, 3-month intervals, min-market-cap 0
(regenerated 2026-07-17 after the alpha-annualization fix — alpha annualizes
over the chained 6-month windows: `years = total_6m / 2`. Baselines generated
before that fix inflated alpha ~2× at `interval_months=3` and are **not**
comparable.)

| Metric | Value |
|---|---|
| Snapshots | 17 |
| Cumulative Return | 187.91% |
| Benchmark Return | 126.25% (SPY) |
| Alpha | 3.06% |
| Sharpe | 0.52 |
| Benchmark Sharpe | 0.54 |
| Win Rate 6M | 56.2% |
| Win Rate 12M | 56.2% |
| Max Drawdown | 34.13% |

### Factor-IC analysis (diagnose before tuning)

`scripts/factor_ic.py` computes per-snapshot cross-sectional Spearman IC of
every factor and candidate metric vs forward 6M returns. Run it **before**
proposing weight changes — a factor with mean IC ≤ 0 adds noise, not signal:

```bash
python scripts/factor_ic.py -t "AAPL MSFT GOOGL ..." --period 5y --no-cache
```

Always pass `--no-cache` when changing `--period`: the cached price history has
no notion of period and would silently poison the run with a shorter window.
Findings from the 2026-07-17 run (26 US mega-caps): profit margin is the most
robust signal (IC ≈ +0.12); 12-1 momentum beats raw 12m in both 3y and 5y
windows; raw 3m/6m momentum, 52w-high proximity, low-vol tilt, and a Value
weight cut (25%→15%) were all **tested and rejected** — univariate factor ICs
do not capture factor interactions, so every change still needs the
before/after backtest below.

### Validation workflow

```bash
# 1. Save baseline BEFORE your change
python scripts/scoring_baseline.py run \
  --tag "before" -r us -n 2 \
  -t "AAPL MSFT GOOGL" \
  --period 5y --interval-months 3 \
  --min-market-cap 0

# 2. Implement your scoring change …

# 3. Save baseline AFTER your change
python scripts/scoring_baseline.py run \
  --tag "after" -r us -n 2 \
  -t "AAPL MSFT GOOGL" \
  --period 5y --interval-months 3 \
  --min-market-cap 0

# 4. Compare
python scripts/scoring_baseline.py compare baseline-before.json baseline-after.json
```

When validating `--include-technical` scoring changes, add the flag to both runs:

```bash
python scripts/scoring_baseline.py run --tag before --include-technical ...
python scripts/scoring_baseline.py run --tag after --include-technical ...
```

### Decision rules

| Outcome | Rule |
|---|---|
| **Ship it** | Alpha ↑ AND Sharpe ↑ AND 12M win rate ↑ |
| **Consider** | Alpha ↑ OR Sharpe ↑ (mixed, review drawdown) |
| **Reject / iterate** | Alpha ↓ AND Sharpe ↓ |

### Key principles

- Use the **same** ticker list, `top_n`, `period`, and `interval-months` for both runs.
- Use `--no-cache` to avoid stale cached financials skewing the comparison.
- Run on a representative subset (e.g. 3-5 well-known US large-caps) for speed; run on the full US universe only for final validation.
- The script stores config + all metrics in a JSON file so comparisons are fully reproducible.
- **Always verify data completeness**: yfinance annual financial statements may return `NaN` for the oldest fiscal year (e.g., FY2021 data is often incomplete). Prefer shorter periods (e.g., `2y` or `3y`) when validating financial-statement-driven scoring changes to ensure all snapshots have complete data.

## yfinance API Verification

Periodically verify that all yfinance fields used by the project still exist:

```python
import yfinance as yf

# Stock fields (AAPL as representative)
t = yf.Ticker('AAPL')
info = t.info
for f in ['trailingPE', 'forwardPE', 'priceToBook', 'pegRatio',
          'returnOnEquity', 'profitMargins', 'earningsGrowth',
          'revenueGrowth', 'debtToEquity', 'currentRatio', 'freeCashflow',
          'dividendYield', 'payoutRatio', 'marketCap', 'currentPrice',
          'regularMarketPrice', 'shortName', 'longName', 'sector',
          'currency', 'exchange']:
    assert f in info, f"Missing stock field: {f}"

# ETF fields (VOO as representative)
etf = yf.Ticker('VOO')
info_etf = etf.info
for f in ['totalAssets', 'netExpenseRatio', 'threeYearAverageReturn',
          'fiveYearAverageReturn', 'beta3Year', 'yield',
          'trailingAnnualDividendYield', 'navPrice',
          'category', 'fundFamily', 'quoteType']:
    assert f in info_etf, f"Missing ETF field: {f}"

# Verify properties
assert t.balance_sheet is not None
assert t.income_stmt is not None
assert t.cashflow is not None
assert len(t.dividends) > 0

# Verify macro indices
for idx in ['^VIX', '^VXN', '^TNX', '2YY=F', '^MOVE', 'DX-Y.NYB']:
    assert not yf.Ticker(idx).history(period='5d').empty
```

**Last verified: 2026-09-25** — All fields present and functional. Findings from that run:

- `annualReportExpenseRatio` is now `None` in yfinance `info` — `netExpenseRatio` is the field that actually resolves (the `_first()` chain in `_fetch_etf()` covers it, so ETF expense ratios still work).
- `funds_data.fund_overview` now only returns `categoryName`, `family`, `legalType`, so the expense-ratio backfill inside `_enrich_etf_info()` is dead code (harmless — guarded and only reached when both `info` fields are missing).

### Dead-ticker sweep

Besides the field checks, run a full-universe sweep to catch delisted/renamed symbols — a dead ticker returns a **one-key `info` dict** (`{"trailingPegRatio": None}`) and an **empty history**:

```python
import yfinance as yf
# batch of 60; a live ticker has >= 1 non-NaN Close row
data = yf.download(tickers, period="5d", group_by="ticker", progress=False)
dead = [t for t in tickers if data[t]["Close"].dropna().empty]
```

**Last sweep (2026-09-25, 383 unique tickers across all 7 universes): 2 dead, both fixed.**

| Symbol | Problem | Replacement |
|---|---|---|
| `SPLG` (US ETF universe) | renamed **2025-10-31** (State Street rebrand); Yahoo returns 404 on the chart endpoint | `SPYM` (expense ratio also dropped 0.03 → 0.02) |
| `CRH.L` (EU universe) | CRH plc delisted from **London and Dublin**; only NYSE listing survives | `CRH` — moved to the **US** universe (S&P 500 member since sep-2024), plus a `us_overrides` entry in `html_export._exchange_mapping()` |

`tests/test_universes.py::test_delisted_and_renamed_symbols_removed` guards both.

### Relevance audit (size + liquidity)

Rank every ticker by **USD market cap** and **3-month average daily turnover** (`volume × close`). FX gotcha: `USD<X>=X` quotes local units **per 1 USD**, so convert with `local / rate` (multiplying inverts it and makes KRW/JPY names look enormous); for `GBp` listings `marketCap` comes back in **GBP** while `price × volume` comes back in **pence**.

**Last audit (2026-09-25, 405 stocks + 95 ETFs): stocks healthy, 5 ETFs removed.**

| Pool | n | Median mcap | Removed |
|---|---|---|---|
| US / EU / Asia / superinvestor stocks | 121 / 99 / 110 / 101 | $170B / $73B / $60B / $82B | none |
| EU ETFs | 38 → 35 | $8.1B | `QANT.L`, `XLES.L`, `XSEN.L` |
| Asia ETFs | 18 → 17 | $6.3B | `ASEA`, `CXSE` |

- Removal criterion for ETFs: **AUM < $500M AND turnover < $1M/day**; index/style coverage survives (`IUES.L`, `QNTM.L`, `FXI`, `MCHI`) and `EWS` replaced `ASEA` as the Southeast Asia representative ($1.25B AUM / $35M per day). Guarded by `tests/test_universes.py::test_micro_etfs_removed`.
- Stocks stay: the three under the $15M/day bar (`RO.SW`, `UHAL`, `KOF`) are mega/large caps — Yahoo just under-reports SIX volume.
- **13 EU UCITS ETFs (`CSPX.AS`, `IWDA.AS`, `EUNL.DE`, `SXR8.DE`, `EQQQ.L`, …) return no `totalAssets`** — they are large iShares/Vanguard funds, a Yahoo data gap (their Size factor scores neutral), **not** irrelevance. Never flag them again; fall back to `sharesOutstanding × navPrice` when `totalAssets` is missing.

## `get_recommendations()` — Programmatic API
```python
from investdaytip import get_recommendations
picks = get_recommendations(top_n=5, region="asia", asset_class="stocks")
picks = get_recommendations(top_n=5, region="superinvestor", asset_class="stocks")
picks = get_recommendations(top_n=5, sector="Financial")
picks = get_recommendations(top_n=5, region="us", scoring_model="quant")
picks = get_recommendations(top_n=5, region="us", data_source="fmp")
```

## Release Workflow

When the user asks to publish a new version, **do not** run `twine upload`
manually — the CI handles PyPI publish + GitHub Release automatically.

Steps:

```bash
# 1. Update version in pyproject.toml and src/investdaytip/__init__.py
# 2. Add changelog entry in CHANGELOG.md
# 3. Commit
git add -A && git commit -m "Bump version to 0.X.0"
# 4. Tag
git tag v0.X.0
# 5. Push tag (triggers CI)
git push && git push origin v0.X.0
```

The CI workflow (`.github/workflows/release.yml`) then:
1. Builds the distribution
2. Publishes to PyPI via trusted publishing
3. Creates the GitHub Release with auto-generated notes

## Seeking Alpha XLSX Import

When a user gives you a Seeking Alpha `.xlsx` path (or asks you to analyze tickers
from a file):

1. **Extract tickers** — use the raw XML method below (openpyxl chokes on Seeking
   Alpha's conditional formatting). Save the CSV to `seeking_alpha_data/` with the
   same filename but `.csv` extension.
2. **Show a preview** — print the ticker count and first few rows (Rank, Symbol, Company Name).
3. **Ask the user** if they want to run `investdaytip` with those tickers.
4. **If yes**, build the command and ask for confirmation before executing.

```python
import zipfile, xml.etree.ElementTree as ET, csv
from pathlib import Path

src = Path("path/to/file.xlsx")
dst = Path("seeking_alpha_data") / src.with_suffix(".csv").name

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

with zipfile.ZipFile(src) as z:
    ss = [
        si.find(f"{NS}r").find(f"{NS}t").text or ""
        if si.find(f"{NS}r") is not None
        else (si.find(f"{NS}t").text or "")
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si")
    ]
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

rows = []
for row in sheet.find(f"{NS}sheetData").findall(f"{NS}row"):
    cells = []
    for c in row:
        v = c.find(f"{NS}v")
        val = v.text if v is not None else ""
        if c.get("t") == "s" and val:
            val = ss[int(val)]
        cells.append(val)
    if cells:
        rows.append(cells)

with open(dst, "w", newline="") as f:
    csv.writer(f).writerows(rows)
print(f"{len(rows)} rows -> {dst}")
tickers = [r[1] for r in rows[1:] if len(r) > 1 and r[1].strip()]
print(f"Tickers ({len(tickers)}): {' '.join(tickers)}")
```
