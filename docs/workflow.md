# The honest workflow

Six steps, in order. Each exists because skipping it produced a wrong number
in a real production system. The library enforces the order where code can
(the single test look, the immutable registration, the no-delete ledger) and
documents it where only discipline can.

## 0. Before anything: hunt your leaks

```python
from nullbar import prefix_replay_check, assert_no_leak, lint_source

assert_no_leak(prefix_replay_check(my_feature_fn, ohlcv_frame), "my_feature")
hits = lint_source(["strategy/"])       # every hit gets a human eye
```

The prefix-replay check recomputes your feature on a prefix of the data and
compares it with the full-sample computation at the same rows. **Any past
value that changes when the future is appended is a leak** — whatever the
source looks like. It catches full-sample normalization, careless
`shift(-n)`, backfills, and the killer: higher-timeframe aggregates mapped
onto the bars *inside* their bucket. Note the check runs two cuts per
fraction at a prime offset, because a cut landing exactly on a bucket
boundary makes bucket leaks invisible.

Rows are matched on the **index**, so a feature that drops warm-up rows is
compared on what it does produce, and non-numeric features are compared by
equality rather than cast to float. Use `assert_no_leak` rather than reading
`report["leak"]` yourself: a check that compared *nothing* is not a clean
bill of health, and it is the one that raises.

In CI, as one command:

```bash
python3 -m nullbar strategy/ features/          # exit 1 on any hit
```

A line you have examined and can defend carries `# noqa: leak <why>`.

**Know what the replay cannot see.** It proves one direction: a feature
whose past changes when the future is appended is leaking. It cannot see a
leak baked into a constant (`MU, SD = df.mean(), df.std()` fitted outside
the callable — i.e. `StandardScaler().fit(X)` before the split) or a
callable that reads a global frame instead of its argument. Both are
prefix-stable and both pass. Cure: hand it a **fit-and-transform** callable
that derives everything from the frame it is given.

## 1. Register before you run

```python
reg = nullbar.Registration(
    name="...", hypothesis="...",
    design={...},                      # every fixed parameter, spelled out
    bar={"cond_name": "requirement"},  # the pass conditions, named
)
sha = reg.freeze("experiments/my_exp.json")
```

Commit the frozen file. The design and bar are now part of a hash — moving
the bar after seeing results is visible forever. If the design must change,
write a NEW registration; history does not get edited.

The seal is tamper-**evident**, not tamper-proof: `verdict()` grades the file
on disk and refuses when memory and disk disagree, and the test-look stamp
carries the registration's sha256 — but anyone with write access can delete
both and start over. Commit them if a third party has to believe them.

## 2. Count every trial

```python
ledger = nullbar.TrialLedger("experiments/trials.jsonl")
ledger.record("my_exp", {"threshold": 0.10}, metrics={"sr": cell_sharpe})
```

Register a `cells_budget` and pass `verdict(..., n_trials=ledger.count())`:
a search that spent more cells than it promised fails, because the
deflation the bar was set against no longer applies.

Every variant you evaluate — including the ones you abandon after one look —
goes in. The ledger is append-only with no delete API, because the count it
holds is the one number human memory reliably shrinks. A best-of-64 search
of pure noise reaches |t| >= 3.35 five percent of the time
(`nullbar.expected_max_abs_t(64, summary=0.95)`); if you don't know your 64,
your t of 2.6 reads as a discovery.

Record `metrics={"sr": ...}` and `ledger.sr_variance()` gives you the other
number deflation needs, instead of inventing one at step 5.

## 3. Null control FIRST

```python
nv = nullbar.null_verdict(entry_mask, fwd_returns, seeds=(0, 1, 2))
assert nv["ok"], nv
```

Shuffle forward returns within each asset and run the identical pipeline.
Read it correctly: the shuffle preserves each asset's marginal, so a
scrambled run reproduces exactly one thing — **the unconditional mean of the
assets the mask holds, weighted the way it holds them**. Not zero (on a
drifting universe a raw |t| against zero can be 9 and mean nothing), and not
an equal-weight buy-and-hold either: the blocks a strategy trades in are
selected, so an equal-weight baseline restricted to those blocks carries the
effect under test. `null_verdict` uses the composition-matched expectation
and reports `hold_baseline` alongside it for context.

`ok` is a MACHINERY verdict — alignment, block assignment, NaN handling and
the shuffle add nothing — and fail-closed: unmeasurable is not a pass. It is
not a claim that the strategy works. That comparison comes next: the real
result against `expected_gross` (did timing add anything?) and against
`hold` (was it worth not simply holding?).

## 4. Cluster your inference

```python
res = nullbar.block_cluster_eval(entry_mask, fwd_returns, block="24h")
```

One entry per asset per block; the block is the unit of inference.
Overlapping forward windows on pooled trades inflated a production result by
~1.9× before this convention caught it. `res["t"]` is the number your bar
should reference.

## 5. Price fills and costs honestly

```python
bracket = nullbar.fill_bracket(entry_mask, limit_px, low_px, fwd_returns)
```

A resting limit order fills only if the market comes to it — and the entries
where it never does are disproportionately your best ones (price ran away).
Measured in production: 92–96% of bids filled, but executed gross was only
0.66–0.79× the assumed gross. Report the touch/through bracket; the truth is
between, and only live resting orders narrow it. All four frames must share
exact axes and the function refuses otherwise — a column reordering here
turned a true gross of 1.0 into 9.0 in testing, silently.

Both sides are modelled: `side="buy"` is a resting bid, measured against the
LOW frame; `side="sell"` a resting ask, measured against the HIGH.

## 6. One test look, then the verdict

```python
reg.spend_test_look("experiments/my_exp.json", results=test_results)
verdict = reg.verdict(results=test_results, n_trials=ledger.count())
```

Write the bar as a **spec** — `{"metric": "t", "op": ">=", "value": 3.0}` —
and `verdict(results=...)` grades it from your metrics directly, so the
frozen promise and the code grading it cannot drift apart. With prose bars
they can, permanently and undetectably: this library's own demo once froze
"null-control |t| ~ 0" and graded it with `worst < 3`, then passed on a
null of 2.77. Pass both a spec and your own boolean and a disagreement
raises `BarMismatchError` instead of quietly picking one.

The held-out evaluation happens once. A second `spend_test_look` raises
`AlreadySpentError` with the timestamp of the first. The verdict checks the
bar *as frozen on disk* — every registered condition must be present and
unambiguously true; extra conditions you invented after seeing the data
cannot rescue a fail.

Conditions are graded fail-closed. `np.False_` is **not** the `False`
singleton, so an identity test would have passed every naturally-computed
condition; anything that is not a clean boolean (`None`, `NaN`, `0`, a
float) fails and is named under `invalid`, and an array-valued condition
raises rather than being guessed at.

---

### The deflation cheat sheet

| You searched | Null E&#124;max t&#124; | Noise clears 5% of the time | So a bar of 3.0 |
|---|---|---|---|
| 1 cell | 0.80 | 1.96 | lots of headroom |
| 4 cells | 1.47 | 2.49 | real headroom |
| 16 cells | 2.08 | 2.95 | some headroom |
| 64 cells | 2.60 | 3.35 | none — noise clears 3.0 more than 5% of the time |

Column 2 is `nullbar.expected_max_abs_t(k)`; column 3 is the same call with
`summary=0.95`. **Use column 3 to set a bar.** The mean is where the middle
of the noise distribution sits, and noise beats it about 45% of the time
(measured: 45.4% over best-of-64) — a "threshold" a coin flip clears is not
one. Both are simulated, seeded, and the large-sample (normal) limit — a lower
bound that errs in the flattering direction; pass `df=n_clusters - 1` for
the honest, fatter-tailed version.

Count cells the way |t| does: a signal tested long AND short is one
two-sided cell, not two. 16 signals × 2 horizons × 2 directions is **32**,
not 64.

Below `df=5` (six clusters) the MEAN is refused outright: the t tails own it
and the answer stops being a threshold anything could pass. Ask for
`summary="median"` and say so, or get more clusters.

Every figure here assumes INDEPENDENT cells — 16 correlated signals × 2
horizons × 2 directions is nowhere near 64 independent, so the threshold is
conservative (the safe direction).

And for Sharpe-based work: `nullbar.dsr(...)` needs the trial count and the
spread of Sharpes across them; passing `None` for either returns `None`,
never 0.0. An unmeasured deflation is not a verdict.
