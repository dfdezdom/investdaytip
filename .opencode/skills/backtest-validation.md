---
description: Backtest-driven scoring validation workflow. Use when the user asks to validate a scoring change, compare backtest baselines, or run the scoring_baseline.py script.
---

# Backtest Validation

Every scoring change must be validated with a **before/after backtest comparison**.

## Workflow

```bash
# 1. Save baseline BEFORE your change
investdaytip backtest:run before

# 2. Implement your scoring change

# 3. Save baseline AFTER your change
investdaytip backtest:run after

# 4. Compare
investdaytip backtest:compare baseline-before.json baseline-after.json
```

## Decision rules

| Outcome | Decision |
|---|---|
| Alpha ↑ AND Sharpe ↑ AND 12M win rate ↑ | **Ship it** |
| Alpha ↑ OR Sharpe ↑ (mixed) | **Consider** — review drawdown |
| Alpha ↓ AND Sharpe ↓ | **Reject / iterate** |

## Key principles

- Use `--no-cache` to avoid stale cached financials skewing results.
- Use the **same** ticker list, `top_n`, `period`, and `interval-months` for both runs.
- Run on a representative subset (3-5 well-known US large-caps) for speed.
- The script stores config + all metrics in a JSON file for reproducible comparisons.
- Prefer shorter periods (e.g., `2y` or `3y`) when validating financial-statement-driven scoring, because yfinance annual financial statements may return NaN for the oldest fiscal year.
