# The honest workflow

Six steps, in order. Each exists because skipping it produced a wrong number
in a real production system. The library enforces the order where code can
(the single test look, the immutable registration, the no-delete ledger) and
documents it where only discipline can.

## 0. Before anything: hunt your leaks

```python
from prereg import prefix_replay_check, lint_source

report = prefix_replay_check(my_feature_fn, ohlcv_frame)
assert not report["leak"], report
hits = lint_source(["strategy/features.py"])   # every hit gets a human eye
```

The prefix-replay check recomputes your feature on a prefix of the data and
compares it with the full-sample computation at the same rows. **Any past
value that changes when the future is appended is a leak** — whatever the
source looks like. It catches full-sample normalization, careless
`shift(-n)`, backfills, and the killer: higher-timeframe aggregates mapped
onto the bars *inside* their bucket. Note the check runs two cuts per
fraction at a prime offset, because a cut landing exactly on a bucket
boundary makes bucket leaks invisible.

## 1. Register before you run

```python
reg = prereg.Registration(
    name="...", hypothesis="...",
    design={...},                      # every fixed parameter, spelled out
    bar={"cond_name": "requirement"},  # the pass conditions, named
)
sha = reg.freeze("experiments/my_exp.json")
```

Commit the frozen file. The design and bar are now part of a hash — moving
the bar after seeing results is visible forever. If the design must change,
write a NEW registration; history does not get edited.

## 2. Count every trial

```python
ledger = prereg.TrialLedger("experiments/trials.jsonl")
ledger.record("my_exp", {"threshold": 0.10})
```

Every variant you evaluate — including the ones you abandon after one look —
goes in. The ledger is append-only with no delete API, because the count it
holds is the one number human memory reliably shrinks. A best-of-64 search
has an expected max |t| of ~2.7 under pure noise
(`prereg.expected_max_abs_t(64)`); if you don't know your 64, your t of 2.7
reads as a discovery.

## 3. Null control FIRST

```python
nulls = prereg.null_control(entry_mask, fwd_returns, seeds=(0, 1, 2))
```

Shuffle forward returns within each asset and run the identical pipeline.
Read it correctly: the shuffle preserves marginals, so the null converges to
the *hold* baseline, not to zero — on drifting assets compare against hold.
If the null shows signal beyond that, your pipeline manufactures effects and
nothing downstream of it can be believed.

## 4. Cluster your inference

```python
res = prereg.block_cluster_eval(entry_mask, fwd_returns, block="24h")
```

One entry per asset per block; the block is the unit of inference.
Overlapping forward windows on pooled trades inflated a production result by
~1.9× before this convention caught it. `res["t"]` is the number your bar
should reference.

## 5. Price fills and costs honestly

```python
bracket = prereg.fill_bracket(entry_mask, limit_px, low_px, fwd_returns)
```

A resting limit order fills only if the market comes to it — and the entries
where it never does are disproportionately your best ones (price ran away).
Measured in production: 92–96% of bids filled, but executed gross was only
0.66–0.79× the assumed gross. Report the touch/through bracket; the truth is
between, and only live resting orders narrow it.

## 6. One test look, then the verdict

```python
reg.spend_test_look("experiments/my_exp.json", results=test_results)
verdict = reg.verdict({"cond_name": bool_outcome, ...})
```

The held-out evaluation happens once. A second `spend_test_look` raises
`AlreadySpentError` with the timestamp of the first. The verdict checks the
bar *as frozen* — every registered condition must be present and true;
extra conditions you invented after seeing the data cannot rescue a fail.

---

### The deflation cheat sheet

| You searched | Null expects max &#124;t&#124; ≈ | So a bar of 3.0 has |
|---|---|---|
| 1 cell | 0.8 | lots of headroom |
| 4 cells | 1.6 | real headroom |
| 16 cells | 2.2 | some headroom |
| 64 cells | 2.7 | almost none |

(`prereg.expected_max_abs_t(k)` — simulated, seeded.) And for Sharpe-based
work: `prereg.dsr(...)` needs the trial count; passing `None` returns `None`,
never 0.0. An unmeasured deflation is not a verdict.
