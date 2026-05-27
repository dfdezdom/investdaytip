# InvestDayTip — Agent Guide

## Build / Test Commands

```bash
source .venv/bin/activate && pip install -e ".[dev]"
python -m investdaytip.main --help   # CLI without install
pytest -q                              # all tests
pytest tests/test_scoring.py -q       # single file
pytest tests/test_scoring.py::test_strong_stock_scores_high -q  # single test
pytest -q -k "strong_stock"           # keyword match
pytest --cov=investdaytip -q          # with coverage
./preview.sh                           # HTTP server (localhost:8000) for HTML reports
```

No linter checked in. Follow PEP 8 + type hints.

## What an Agent Likely Misses

### Conventions
- `from __future__ import annotations` in every module
- `Optional[float]` not `float | None` — project style
- `AssetData = Union[StockData, EtfData]` alias (`data_source.py:109`)
- `Literal["all", "stocks", "etfs"]` / `Literal["all", "us", "eu", "asia"]` / `Literal["all", "USD", "EUR", …]` for constrained params
- `Iterable[str]` for function params, `list[str]` for returns
- Single-quoted strings; dataclass mutable defaults use `field(default_factory=...)`
- `_safe_get()` helper to extract/convert `Optional[float]` from yfinance info dicts
- `_suppress_stderr()` context manager wraps every yfinance call

### Architecture
- **`scoring.py` must stay pure** — no network, no I/O, no side effects
- All network calls belong in `data_source.py` (yfinance wrapper)
- `fetch_asset()` is the primary entry point; `fetch_stock()` is a backwards-compatible alias
- New ticker universe → `*_universe.py` module wired in `recommender._build_universe()`
- `_build_universe()` now also accepts a `currency` param; when currency != "all" and region == "all", it derives the region from the currency (USD→us, EUR→eu, JPY→asia) to avoid unnecessary API calls
- Universe export names are inconsistent: `DEFAULT_UNIVERSE`, `DEFAULT_EU_UNIVERSE`, `DEFAULT_ETF_UNIVERSE`, `DEFAULT_EU_ETF_UNIVERSE` vs `ASIA_UNIVERSE`, `ASIA_ETF_UNIVERSE` (no `DEFAULT_` prefix). Check each module before importing.
- `ScoredAsset` is the unified output; `ScoredStock` is a backwards-compatible alias (`scoring.py:55`)
- `score_asset()` dispatches to `score_stock()` / `score_etf()` based on type
- Default `min_market_cap` filter: $2B (applied against native-currency figures, approximate for non-USD)
- `RISK_FREE_RATE = 0.045` in `data_source.py` for ETF Sharpe proxy
- `recommend()` accepts `progress_cb(done, total, ticker)` for UI feedback; uses `ThreadPoolExecutor`

### Scoring
- Stock breakdown keys: `Quality` (35%), `Value` (25%), `Health` (20%), `Trend` (20%)
- ETF breakdown keys: `Returns` (40%), `RiskAdj` (25%), `Size` (15%), `Cost/Yield` (20%)
- Missing data → neutral 50 score (never crash); yfinance errors caught per-ticker, stored in `errors` list

### CLI & Export
- CLI entry: `investdaytip` (installed) or `python -m investdaytip.main`
- Programmatic API: `from investdaytip import get_recommendations`
- HTML filename pattern: `investDayTip[-<tag>]-yyyymmdd-hhmm.html` — tag auto-derived from tickers-file stem (`_export_filename_tag_from_tickers_file()`)
- Ticker suffixes dictate exchange/region mapping (`html_export._exchange_mapping()`)
- URL building accepts `exchange_hint` param (yfinance exchange codes like `NMS`, `NYQ`) for correct Google Finance / TradingView links (`html_export._normalize_exchange_hint()`)
- Ticker files: supports lines, spaces, commas, and `#` comments
- HTML entrypoint: `html_export.export_recommendations_html(results, destination, ...)` — also accepts metadata kwargs (`top_n`, `asset_class`, `region`, `tickers`, `tickers_file`)

### Testing
- Pure unit tests — never make live network calls; construct `StockData`/`EtfData` directly
- Use `tmp_path` fixture for file-based tests (HTML export, ticker file parsing)
- Build system: hatchling (`pyproject.toml`); requires Python >=3.10

### Related Files
- `.github/copilot-instructions.md` — parallel instruction source for GitHub Copilot; keep in sync
- `CONTRIBUTING.md` — contributor guidelines (pure scoring, PR workflow)

### Key Files

| Purpose | Path |
|---------|------|
| CLI entry point | `src/investdaytip/main.py` |
| Programmatic API | `src/investdaytip/__init__.py` |
| Orchestration + universe builder | `src/investdaytip/recommender.py` |
| Data fetching + dataclasses | `src/investdaytip/data_source.py` |
| Stock & ETF scoring | `src/investdaytip/scoring.py` |
| HTML export | `src/investdaytip/html_export.py` |
| US stock universe | `src/investdaytip/universe.py` |
| US ETF universe | `src/investdaytip/etf_universe.py` |
| EU stock universe | `src/investdaytip/eu_universe.py` |
| EU ETF universe | `src/investdaytip/eu_etf_universe.py` |
| Asia stock universe | `src/investdaytip/asia_universe.py` |
| Asia ETF universe | `src/investdaytip/asia_etf_universe.py` |
| Tests | `tests/` |
| Example ticker files | `tickers-files-examples/` |
| Install (Unix) | `install.sh` |
| Install (Windows) | `install.bat` |
| Preview server | `preview.sh` |
