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

## 7. Hand someone the record

```bash
nullbar report experiments/my_exp.json --ledger experiments/trials.jsonl
```

One self-contained HTML file — no assets, no scripts, prints to PDF — that
carries the frozen registration and its sha256 (verbatim, so a reader can
re-hash it), the trial count against the registered cell budget, the null
control, the clustered result, the fill bracket, the deflation with its
**95th-percentile** threshold, when the single test look was spent and
whether it is still bound to this registration, and the bar with each
condition's observed value beside its verdict.

Everything in it comes off disk. The report never recomputes a result from
market data, because a report that recomputes can quietly report something
the registration never graded. The only arithmetic at report time is the
deflation simulation, from the recorded cell and cluster counts, seeded.

Build the payload with `evidence()` so the record is complete:

```python
measured = nullbar.evidence(res, null=nv, fills=bracket,
                            net=res["gross"] - cost, sr=cell_sharpe,
                            conditions={"operator_sane": True})
reg.spend_test_look("experiments/my_exp.json", results=measured)
```

Spending the look on a bare result is the common mistake — this library's
own demo did it, and the stamp then could not re-derive two of its three
conditions. What is not in the payload is not in the report, and the report
says so out loud: a missing piece is listed under *What this record does not
contain*.

Gaps the verdict **depends on** degrade it to **INCOMPLETE**, and
`nullbar report` exits non-zero — a claimed multi-cell search with no trial
ledger is the case, because the deflation the bar was set against cannot be
computed at all. Gaps that do not bear on the verdict are printed and do not
block it: a single-cell registration needs no ledger, and demanding an
anchor would put PASS out of reach for anyone not using git. An incomplete
record must never be indistinguishable from a passing one — least of all in
CI.

Four statuses, and only one of them is good news:

| Status | Means |
|---|---|
| `PASS` | every registered condition met, graded against the frozen file |
| `FAIL` | a condition was not met, was graded by a non-boolean, or the search blew its cell budget |
| `INCOMPLETE` | the record does not establish a verdict — **not** a pass |
| `CONTRADICTED` | the frozen bar and the recorded grading disagree |

The report inherits the seal's limit: it is tamper-evident, not
tamper-proof. It binds the look to the registration by hash and says so on
its face, but a reader who must not trust the researcher needs the frozen
file and the stamp anchored somewhere the researcher does not control.

## 8. Anchor it, if someone else has to believe you

```bash
nullbar anchor experiments/my_exp.json --commit    # BEFORE you run
# ... the study runs, the look is spent ...
nullbar anchor experiments/my_exp.json --commit    # after
nullbar verify experiments/my_exp.json             # exit 1 if it does not hold
```

Steps 1–7 are enforced by your own discipline and your own clock. Every
timestamp in the record is written by you, so a study run first and
registered afterwards — with a bar chosen to fit the answer it already
knows — produces a flawless report and an intact seal. That is the hole
anchoring closes.

Anchor the registration **before** you run and the stamp **after**, and the
registration's commit is an ancestor of the stamp's. Changing that order
means rewriting history, which changes every descendant hash and, once
pushed, is a force-push someone else's server saw. `verify` checks four
things: the committed bytes are the bytes being graded, the registration
commit precedes the test-look commit, both are still reachable from HEAD,
and whether any remote-tracking ref contains them. The report renders all
four, and a broken anchor makes the verdict `CONTRADICTED` — the committed
record and the graded record disagreeing is the same class of fact as a bar
that contradicts its own grading.

Read the limits as carefully as the guarantees:

- **It does not prove wall-clock time.** Commit dates are self-reported and
  `GIT_COMMITTER_DATE` forges them in one environment variable. Only a push
  to a host you do not control was witnessed by anyone else — and by a log
  nobody can audit. For time itself, anchor the hash with an RFC-3161
  timestamp or a transparency log; git is the cheapest option, not the
  strongest.
- **A local-only repository proves nothing to a third party.** Rewind far
  enough and the anchor record reverts with everything else, leaving
  nothing to disagree with. The report says "local only" out loud when no
  remote has seen the commits, and there is a test pinning exactly this
  blind spot rather than papering over it.
- **It cannot show you had not already seen the test window.** Ordering of
  documents is not ordering of knowledge. Nothing short of somebody else
  holding the held-out data fixes that one — which is a custody
  arrangement, not a library.

Both commits also carry the anchor record itself (`*.anchor.json`), because
untracked it would be invisible to the clone a reader actually checks.

**The trial ledger is anchored too**, and for the same reason the bar is:
the two things a reader has to be able to trust are the bar and the number
of cells it was set against. Left uncovered, a ledger can be quietly
*shrunk* — the budget check then passes against a search that never
happened, every deflation figure is divided by a smaller number, and nothing
else in the record disagrees. The ledger is append-only by design, so it is
checked as a **prefix** rather than for byte equality: rows may be added,
never edited or removed. Recording another trial keeps the anchor intact;
rewriting or deleting one breaks it. A record anchored before ledgers were
covered still reads `intact` and says the ledger is uncovered, because
marking every older record as tampered would teach a reader to ignore the
word.

Anchor the ledger **when the search finishes and before you spend the
look**. Anchoring it later — with the stamp, say — records the right number
in a commit carrying no evidence that it predates the result, and ordering
is the only thing an anchor attests. Pass `--ledger` if it is not named
after the registration; coverage by filename convention fails silently when
the convention does not hold.

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
