# prereg

[![tests](https://github.com/brunopereira81/prereg/actions/workflows/test.yml/badge.svg)](https://github.com/brunopereira81/prereg/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**Pre-registration and honest statistics for trading research.**

Most backtesting tools help you find an edge. This library assumes you will
fool yourself while trying — because in two years of production algorithmic
trading, that is what happened to us, over and over, despite code review,
2,000+ tests, and genuine care. `prereg` is the machinery that caught it,
extracted into a standalone package.

## The one-day story this library made possible

On 2026-08-11 we designed a new ML model (a cross-sectional transformer over
379 crypto assets), **pre-registered** its evaluation — target, architecture,
splits, and a three-condition pass bar, committed before training — trained
it, and evaluated it **once** on a test window the training code could not
read even by bug.

The model achieved 5× the rank correlation of every predecessor. It also
failed all three bar conditions: its extra correlation lived in the middle of
the ranking, and the only part of a ranking that can pay trading costs is the
tail — which a four-line moving average already owned. Design to honest
verdict: **one day.** Without this machinery, that same question had
previously consumed months and produced numbers we later had to retract.

## What's inside, and the production bug behind each piece

| Module | What it does | The bug it exists because of |
|---|---|---|
| `registration` | Freeze design + pass bar before results; **one** test look, enforced | "One more epoch, one more threshold" after seeing test data |
| `ledger` | Append-only trial count (no delete API) | A t=2.68 celebrated against a best-of-64 noise threshold of ~2.7 |
| `stats` | Clustered t, PSR/DSR in strictly per-period units; `dsr` returns `None`, never 0, for unknown trial counts | Overlapping windows inflating results 1.9×; a gate that logged PSR=0.000 for months because annualized and per-period units were mixed |
| `evaluate` | Block-clustered evaluation + shuffled null controls | Pipelines that "find" effects their own machinery created |
| `fills` | Touch/through fill brackets for resting orders | Assumed fills overstated executed gross 1.3–1.5× — the entries that never fill are the best ones |
| `leaklint` | Static lookahead lint + **prefix-replay check** | A multi-timeframe resampling leak that fed +23h of future into features, survived two years and every review, and explained a deployed model's entire measured edge |

The prefix-replay check deserves a sentence: recompute any feature on a data
prefix and compare with the full-sample computation at the same rows. **Any
feature whose past changes when the future is appended is leaking**, whatever
its source looks like. This one test, run on day one, would have saved us a
year.

## Quickstart

```python
import prereg

# 1. Register before you run
reg = prereg.Registration(
    name="mean-reversion-24h",
    hypothesis="bottom-decile dist_ma168 mean-reverts over 24h",
    design={"hold_bars": 24, "entry_pct": 0.10, "cost_pct": 0.230},
    bar={"null_flat": "null-control |t| ~ 0",
         "t3": "clustered t >= 3.0 on 24h blocks",
         "beats_hold": "net beats unconditional exposure"},
)
reg.freeze("experiments/mr24.json")          # hashed; edits now visible

# 2. Count every variant you evaluate
ledger = prereg.TrialLedger("experiments/trials.jsonl")
ledger.record("mr24", {"entry_pct": 0.10})

# 3. Null control FIRST, then the real number, clustered
nulls = prereg.null_control(entry_mask, fwd_returns)      # must be flat
result = prereg.block_cluster_eval(entry_mask, fwd_returns)

# 4. Price fills honestly
bracket = prereg.fill_bracket(entry_mask, limit_px, low_px, fwd_returns)

# 5. Deflate by what you actually searched
d = prereg.dsr(observed_sr, n=n_periods, n_trials=ledger.count(),
               sr_variance=var_across_trials)

# 6. Spend the single test look, on the record
reg.spend_test_look("experiments/mr24.json", results={...})
print(reg.verdict({"null_flat": True, "t3": False, "beats_hold": True}))
```

## What this library will not do

It will not find you an edge. Ours, measured with these exact tools across
architecture, features, horizons, and training breadth, was zero net of
costs — and we can prove it, which is the point. If your strategy survives
this harness, you have something. If it doesn't, you found out for the price
of compute instead of capital.

## Docs & examples

- **[The honest workflow](docs/workflow.md)** — the six steps, each annotated
  with the production failure it prevents, plus the deflation cheat sheet.
- **[examples/01_full_workflow.py](examples/01_full_workflow.py)** — the whole
  sequence end-to-end on synthetic data; runs in seconds, CI-tested.
- **[examples/02_catch_a_leak.py](examples/02_catch_a_leak.py)** — three
  features, two leaks, one 50ms check; includes the leak that inspired the
  library.
- **[The leak that survived two years](docs/posts/the-leak-that-survived-two-years.md)**
  — the full story.

## Status

`v0.1.0` — extracted 2026-08-12 from a live production system
(Coinbase spot, TimescaleDB, 2,100+ tests). API will move; the philosophy
won't. MIT licensed — the statistics stay open, permanently.
