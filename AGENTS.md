# InvestDayTip — Agent Guide

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
|---|---|---|---|
| CLI + public API | `main.py` | argparse + `get_recommendations()` re-exported from `__init__.py` |
| Advisor | `advisor.py` | interactive market pulse, portfolio review, buy recs, CLI subcommand |
| Orchestration | `recommender.py` | builds universe, ThreadPoolExecutor fetches, scores, filters, sorts |
| Data fetching | `data_source.py` | yfinance wrapper, dataclasses (`StockData` / `EtfData` / `AssetData`). **All network I/O lives here.** |
| Caching | `cache.py` | SQLite cache with per-thread connections, WAL mode, write lock |
| Scoring | `scoring.py` | pure functions only — no I/O, no side effects |
| HTML export | `html_export.py` | self-contained report with inline CSS/JS |
| Sentiment | `sentiment.py` | CNN Fear & Greed Index, no yfinance (uses `urllib`) |
| Universes | `*_universe.py` (7 modules) | curated ticker lists wired in `recommender._build_universe()` (deduplicated case-insensitively) |
| Tests | `tests/` | 8 files, no live network calls (autouse network guard in `conftest.py`) |
| OpenCode agent | `.opencode/agents/advisor.md` | advisor subagent: permissions, interactive flow, execution methods, and interpretation guide |

Data flow: `CLI → recommender → data_source (yfinance) → scoring → html_export / Rich table`

## Conventions & Gotchas

- `from __future__ import annotations` in every annotated module (not in `__init__.py` or universe files)
- `Optional[float]` for dataclass fields; `Iterable[str] | None` for function params with `from __future__ import annotations`
- `field(default_factory=list/dict)` for mutable defaults on dataclasses
- `_safe_get(info, key)` — extracts/validates `Optional[float]` from yfinance info dicts; rejects non-finite values (NaN **and** ±inf) via `math.isfinite`
- `_first(*values)` — returns the first non-`None` value; used for fallback chains so a legitimate `0.0` is preserved (an `or` chain would discard it)
- `_suppress_stderr()` context manager wraps **every** yfinance call — without it, yfinance spams stderr
- Asset type dispatch: `score_asset()` → `score_stock()` / `score_etf()` via `isinstance`
- `ScoredAsset` unified output; `ScoredStock = ScoredAsset` backwards-compatible alias
- Universe export naming: US + EU use `DEFAULT_` prefix (`DEFAULT_UNIVERSE`, `DEFAULT_EU_ETF_UNIVERSE`), Asia does not (`ASIA_UNIVERSE`, `ASIA_ETF_UNIVERSE`), Superinvestor uses `SUPERINVESTOR_UNIVERSE`
- `_build_universe()` accepts `currency` param; when `currency != "all"` and `region == "all"`, it derives region from currency (USD→us, EUR→eu, JPY→asia) to reduce API calls. It deduplicates the merged pools case-insensitively (overlapping universes share tickers like `VXUS`/`IEMG`)
- Superinvestor region is stocks-only (no ETF universe); tickers are US-listed with direct 13F data from DataRoma

### Caching
- `CacheDB` in `cache.py`: SQLite with `threading.local()` per-thread connections, WAL mode, write lock via `threading.Lock`
- Four cache entry types:
  - `{ticker}:info` (fundamentals, TTL 1d)
  - `{ticker}:history` (prices, TTL 5min)
  - `_global:fear_greed` (CNN Fear & Greed Index, TTL 1h)
  - `superinvestor:holdings` (DataRoma aggregated data, TTL 7 days)
- `fetch_asset()` defers cache-write until both info and history are fetched (atomic snapshot); partial results cached on history failure
- Connections are tracked so `CacheDB.close_all()` / module-level `close_db()` can release **worker-thread** connections; `recommend()` calls `close_db()` in a `finally` after the pool tears down
- `--no-cache` flag disables cache read/write; `--cache-clear` drops all entries
- `--superinvestor` flag enables the DataRoma superinvestor cache warm-up (~80 HTTP requests) and the "Superinvestors" column in both HTML and CLI output; disabled by default
- Tests auto-disable cache via `conftest.py::disable_cache` (autouse); an autouse `no_network` guard fails fast on unmocked `yf.Ticker`; `enabled_temp_cache` fixture backs cache tests with a `tmp_path` DB (never the real `~/.investdaytip`)

### Rate Limits & Error Handling
- `fetch_asset()` retries on `YFRateLimitError` with delays [10, 30, 60]s then returns error dataclass
- Missing data → neutral 50 (never crashes); yfinance errors caught per-ticker, stored in `errors` list

### CLI Quirks
- `--export-html` uses `nargs="?"` with `const=""` — no arg means auto-generated filename `investDayTip[-<tag>]-yyyymmdd-hhmm.html`; tag derived from tickers-file stem (stopwords filtered)
- `advisor` subcommand duplicates many flags from main parser but some default to `None` for interactive prompts
- Ticker files: supports newlines, spaces, commas, and `#` comments; `_merge_ticker_lists()` deduplicates case-insensitively preserving first-occurrence casing
- `--min-market-cap` filter: $2B default (e.g. `1B`, `500M`, `0` to disable); applied against native-currency figures, approximate for non-USD. When the filter is active, assets with **missing** market cap are excluded (a missing figure can't satisfy the filter), and history fetch is skipped for them
- Currency filter keeps assets whose `currency` is `None` (a missing field shouldn't silently drop an otherwise-valid candidate)
- `-r`/`--region` and `-c`/`--currency` use `nargs="+"` — pass multiple values: `-r us eu`, `-c USD EUR`. Both `str` and `list[str]` accepted programmatically.
- `--superinvestor` controls DataRoma scraping (~80 HTTP requests) and the "Superinvestors" column; the curated `SUPERINVESTOR_UNIVERSE` tickers are **always** included in the stock pool (they are quality stocks), but the manager-count data and column are only fetched/displayed when the flag is present

### Advisor Module
- `market_regime()` fetches `^VIX` and `^VXN` via yfinance; thresholds: ≤15 bullish, ≤25 neutral, ≤35 bearish, >35 crash
- `bubble_risk()` computes VIX percentile over trailing 2 years; <15% → high (complacency), 15-30% → medium, >30% → low
- `macro_regime()` — composite 0-100 macro health score combining VIX + 10Y-2Y yield curve + MOVE index (bond vol) + DXY (dollar strength) + CNN Fear & Greed; regimes: ≥70 healthy→buy, ≥45 neutral→hold, ≥25 warning→hold, <25 danger→sell
- `portfolio_review()` loads tickers from file with `_load_tickers_from_file()`, passes through `recommend()` scoring, returns categorized results
- `run_comprehensive()` is the programmatic API for multi-region/asset-class analysis; exports HTML per combination to `advisor_recommendations/`
- `advisor_main()` writes the final HTML report to `advisor_recommendations/recommendations_advisor_<timestamp>.html`

### ETF Data Specifics
- Expense ratio has 3 fallback sources: `annualReportExpenseRatio` → `netExpenseRatio` → `funds_data.fund_overview`
- Sharpe proxy: `(return_12m - RISK_FREE_RATE) / volatility_1y` where `RISK_FREE_RATE = 0.045`
- `EtfData.sector` returns `category`; `EtfData.market_cap` returns `total_assets` (AUM)

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

### Scoring Weights
- **Stocks**: Quality 35%, Value 25%, Health 20%, Trend 20%
- **ETFs**: Returns 40%, RiskAdj 25%, Size 15%, Cost/Yield 20%

## OpenCode Agent

The `advisor` subagent is configured in `.opencode/agents/advisor.md`. It defines:

- **Permissions:** bash/read allowed, write with confirmation
- **Required flow:** always ask the user before running any analysis
- **Execution methods:** `macro_regime()` (VIX + yield curve + bond vol + DXY + Fear & Greed) for full macro pulse (returns `action`: buy/hold/sell), `market_regime()` + `bubble_risk()` for quick VIX-only pulse, `run_comprehensive()` for multi-region, or interactive CLI `investdaytip advisor`
- **Output format:** clean markdown (never raw Rich tables)
- **Interpretation rules:** VIX thresholds (≤15 bullish, ≤25 neutral, ≤35 bearish, >35 crash), bubble risk (VIX percentile <15% → complacency), macro regime (composite 0-100: ≥70 healthy→BUY, ≥45 neutral→HOLD, ≥25 warning→HOLD, <25 danger→SELL), scores, portfolio signals

## Testing Notes
- Construct `StockData` / `EtfData` directly — never call yfinance in tests
- Use `tmp_path` fixture for HTML export and ticker-file tests
- Mock `investdaytip.advisor.yf.Ticker` / `investdaytip.advisor._fetch_index` for advisor tests; mock `investdaytip.recommender.fetch_asset` (and `close_db`) for recommender tests
- `tests/test_universes.py` enforces ticker-format/no-duplicate integrity across all 7 universe modules

## `get_recommendations()` — Programmatic API
```python
from investdaytip import get_recommendations
picks = get_recommendations(top_n=5, region="asia", asset_class="stocks")
picks = get_recommendations(top_n=5, region="superinvestor", asset_class="stocks")
```
