# vol

Research console: does a stock move more over the next 5 trading days than expected?
Paper/research only. No broker connection anywhere in this repo. Scores are model probabilities, not advice.

## Two labels — don't confuse them
- `rv_expand` (backtestable, free): |5-day move| > trailing-20-day realised vol × √5. A **proxy**. Most of its
  signal is "earnings inside the window", which options already price.
- `beat_implied` (the real question): |5-day move| > ATM straddle implied move. Only labelable from the day
  `record_chain.py` starts recording (2026-10-13). Accrues ~1 row/ticker/day. Train on it once ≥500 rows.

## Pipeline (all free, yfinance)
```
.venv/bin/python record_chain.py loop   # daily 16:15 ET options chain → chain table. keep running.
.venv/bin/python data.py                # bars, earnings dates, SPY, ^VIX
.venv/bin/python label.py               # rv_expand (+ beat_implied where chain exists)
.venv/bin/python train.py [rv_expand|beat_implied]
.venv/bin/python serve.py               # console at http://127.0.0.1:8000
```
Tickers: edit `TICKERS` in data.py and record_chain.py.
Tests: `for t in test_*.py; do .venv/bin/python $t; done`

## Backtest result 2026-10-13 (rv_expand, test = 2023→)
AUC 0.67. p>0.5: 433 picks, 70% hit vs 32% base — but 80% of picks are earnings weeks.
Excluding earnings weeks: 87 picks, 63% hit vs 29% base. Small n; treat as a hint.
