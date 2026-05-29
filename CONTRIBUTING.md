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
|---|---|---|
| `src/investdaytip/scoring.py` | Pure scoring functions for stocks & ETFs |
| `src/investdaytip/data_source.py` | yfinance wrapper, dataclasses |
| `src/investdaytip/recommender.py` | Concurrent orchestration |
| `src/investdaytip/main.py` | CLI entry point (`investdaytip`) |
| `src/investdaytip/advisor.py` | Interactive advisor subcommand (`investdaytip advisor`) |
| `src/investdaytip/*_universe.py` | Curated ticker universes |
| `tests/` | Unit tests (pure, no network) |

## Areas Open for Contribution

- 🌐 **New regions** — Asian markets (Japan, Hong Kong), Latin America, etc.
- 💱 **Currency normalization** — convert prices/market caps to a chosen base currency
- 📐 **Scoring tuning** — propose better factor weights or add new metrics (FCF yield, EV/EBITDA, Piotroski F-Score…)
- 🔌 **Alternative data sources** — pluggable backends besides yfinance (Alpha Vantage, Finnhub…)
- 🧪 **More tests** — edge cases, integration tests with recorded fixtures
- 🖥️ **Output formats** — JSON, CSV export for the CLI

## Guidelines

- Follow [PEP 8](https://pep8.org/) and use type hints.
- **Keep `scoring.py` pure** — no network or I/O. Add network code only in `data_source.py`.
- New ticker universes should be a new module ending in `_universe.py` and wired up in `recommender._build_universe`.
- Add tests for any new scoring logic. Mock or construct `StockData`/`EtfData` directly — do not hit the network in tests.
- Keep commits focused and use clear commit messages.
- Update the README if your change affects usage, options, or scoring weights.

## Submitting a Pull Request

1. Ensure `pytest -q` passes.
2. Open a pull request against `main` with a clear description.
3. Link any related issue using `Closes #issue-number`.

## Code of Conduct

Be respectful and constructive. We follow the [Contributor Covenant](https://www.contributor-covenant.org/).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
