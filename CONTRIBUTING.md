# Contributing to InvestDayTip

Thank you for considering contributing! 🎉

## Getting Started

1. Fork the repository and clone your fork.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```
3. Run the test suite to confirm the baseline works:
   ```bash
   pytest -q
   ```
4. Create a branch for your change:
   ```bash
   git checkout -b feat/your-feature-name
   ```

## Project Layout

| File | Purpose |
|---|---|
| `src/investdaytip/scoring.py` | Pure scoring functions for stocks & ETFs (`classic` + `quant` models) |
| `src/investdaytip/financial_health.py` | Piotroski F-Score, Altman Z, YoY-improvement flags — pure functions |
| `src/investdaytip/data_source.py` | yfinance wrapper, dataclasses (`StockData` / `EtfData` / `AssetData`) |
| `src/investdaytip/data_source_yahooquery.py` | yahooquery batch data source (`--data-source yahooquery`) |
| `src/investdaytip/data_source_fmp.py` | FMP data source (`--data-source fmp`) |
| `src/investdaytip/data_source_stockfit.py` | StockFit PIT/live source + fundamental insights |
| `src/investdaytip/cache.py` | SQLite caching layer (per-thread connections, WAL mode) |
| `src/investdaytip/recommender.py` | Concurrent orchestration |
| `src/investdaytip/main.py` | CLI entry point (`investdaytip`) |
| `src/investdaytip/advisor.py` | Interactive advisor subcommand (`investdaytip advisor`) |
| `src/investdaytip/backtest.py` | Historical scoring validation (`investdaytip backtest`) |
| `src/investdaytip/deep_dive.py` | Per-ticker report (`investdaytip deep-dive`) |
| `src/investdaytip/html_export.py` | Self-contained HTML report export |
| `src/investdaytip/risk_signals.py` | Keyless "devil's advocate" risk layer (context, never scored) |
| `src/investdaytip/sentiment.py` | CNN Fear & Greed Index (no yfinance) |
| `src/investdaytip/dataroma.py` | DataRoma superinvestor 13F data (`--superinvestor`) |
| `src/investdaytip/*_universe.py` | Curated ticker universes |
| `tests/` | Unit tests (pure, no network; conftest.py disables cache automatically) |

## Areas Open for Contribution

- 🌐 **New regions** — Asian markets (Japan, Hong Kong), Latin America, etc.
- 💱 **Currency normalization** — convert prices/market caps to a chosen base currency
- 📐 **Scoring proposals** — new metrics (FCF yield, EV/EBITDA…) or weight changes, **with evidence** (see below)
- 🔌 **Alternative data sources** — pluggable backends besides yfinance (Alpha Vantage, Finnhub…)
- 🧪 **More tests** — edge cases, integration tests with recorded fixtures
- 🖥️ **Output formats** — JSON, CSV export for the CLI

> **Scoring changes have a high bar.** This is a quantitative project: every
> scoring change must ship with factor-IC evidence (`scripts/factor_ic.py`) and
> a before/after backtest comparison (`scripts/scoring_baseline.py`) on the same
> universe and config — see `AGENTS.md` § *Backtest-Driven Scoring Validation*.
> Proposals without that evidence will be discussed as issues first, and final
> validation runs on the full universe are executed by the maintainer. Note that
> some candidates have already been tested and **rejected** (e.g. Piotroski
> F-Score and Altman Z as scored factors) — check `AGENTS.md` before re-proposing.

## Guidelines

- Follow [PEP 8](https://pep8.org/) and use type hints. Lint with `ruff check src tests` and type-check with `mypy` (both in the `dev` extra). Keep `Optional[...]` for dataclass fields — ruff's `UP` rule is intentionally off.
- **Keep `scoring.py` pure** — no network or I/O. Add network code only in `data_source.py`.
- New ticker universes should be a new module ending in `_universe.py` and wired up in `recommender._build_universe`.
- Add tests for any new scoring logic. Mock or construct `StockData`/`EtfData` directly — do not hit the network in tests.
- Keep commits focused and use clear commit messages.
- Update the README if your change affects usage, options, or scoring weights.
- **AI-generated/-assisted code is welcome only if you can explain and defend every line.** Disclose AI tool usage in the PR template. PRs whose changes the author cannot explain will be closed.

## Submitting a Pull Request

1. Ensure `pytest -q`, `ruff check src tests`, and `mypy` all pass.
2. Open a pull request against `main` with a clear description.
3. Link any related issue using `Closes #issue-number`.

## Code of Conduct

Be respectful and constructive. We follow the [Contributor Covenant](https://www.contributor-covenant.org/).

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT](LICENSE) license (inbound == outbound). You keep the copyright
of your work — there is no copyright assignment and no CLA.
