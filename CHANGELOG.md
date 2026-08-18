# Changelog

All notable changes to this project. Dates are UTC.

## [0.3.0] — 2026-08-18

The remaining findings from the first audit (F1–F20, 2026-08-17); v0.2.0
covered the second review's list, which restated only part of it. Two of
these are silent-wrong-answer defects and one is the design gap that had
already bitten this project's own launch material.

### Fixed — correctness

- **`clustered_t` inflated t by `sqrt(n_total / n_finite)`.** Cluster means
  come from `groupby().mean()`, which skips NaN, but `n` counted every
  cluster label including those that contributed no finite observation, so
  the standard error was divided by a `sqrt(n)` that was too large — the
  exact inflation the function exists to remove, arriving through the input.
  Trailing rows with no forward return yet are the ordinary way it happens
  (30 real + 30 empty clusters read t = 1.680 instead of 1.188). Empty
  clusters are now dropped, and a null cluster LABEL raises rather than
  being silently dropped by pandas.
- **A non-datetime index bucketed every row into 1970.** `pd.to_datetime`
  reads an integer index as nanoseconds since the epoch, so
  `block_cluster_eval` on a `RangeIndex` returned one cluster, no error, and
  a number that looked like a result. It now raises `TypeError`.
- **The bar as written and the bar as evaluated could diverge.** The
  registration stored prose and `verdict()` accepted caller-computed
  booleans, with nothing connecting them — and it had already happened in
  this project's flagship demo, which froze "null-control |t| ~ 0" and
  graded it with `worst < 3`, passing on a null of 2.77. A bar entry may now
  be a spec — `{"metric": "t", "op": ">=", "value": 3.0}`, optionally
  `"abs": True` — which `verdict(results=...)` grades directly from your
  metrics. Passing both a spec and your own boolean raises
  `BarMismatchError` on disagreement instead of silently picking one. An
  absent metric is `missing`, never False; a NaN metric fails.
- **`cells_budget` is wired up.** It was hashed into the seal and read by
  nothing. `verdict(..., n_trials=ledger.count())` now fails a search that
  spent more cells than it registered, because the deflation the bar was set
  against no longer applies.
- **Re-running an identical registration no longer accuses you of editing
  history.** `created_at` is part of the hash, so a re-run after a crash, or
  twice in CI, raised `FileExistsError` on a byte-identical promise.
  Comparison now ignores the timestamp; a moved design or bar is still
  refused, and the file's hash stays the one that counts.

### Fixed — leak detection

- Three lookahead shapes the static lint missed: `shift(periods=-1)`,
  `np.roll(arr, -1)` and `merge_asof(direction="forward")`.
- **The prefix-replay check's false-negative class is now stated in the
  docstring, in `docs/workflow.md` and in the README**, with a regression
  test pinning it: a transform fitted on the whole sample OUTSIDE the
  callable (i.e. `StandardScaler().fit(X)` before the split) and a callable
  that reads a global frame instead of its argument are both prefix-stable
  and both pass. The cure is to hand it a fit-and-transform callable. A leak
  the function cannot see is a leak this check cannot report, and no number
  of cuts changes that.

### Other

- Direct tests for `shuffle_within_columns`, `through_mask` and `LintHit`,
  which were exported but only ever exercised through their callers.
- `dist/` appeared twice in `.gitignore`.
- Tags: v0.1.0 and v0.2.0 are tagged retroactively, v0.3.0 at this commit.

## [0.2.0] — 2026-08-18

**Renamed from `prereg` to `nullbar`.** The PyPI name `prereg` was claimed on
2026-08-18 by an unrelated project (an OSF-oriented pre-registration CLI);
this package had not yet been uploaded. Both the distribution and the import
package are renamed, so the two can coexist in one environment. Update
`import prereg` → `import nullbar`.

Everything below came out of an external audit of v0.1.0. Two of the findings
were in the flattering direction, which is the direction this library exists
to refuse.

### Fixed — correctness

- **`Registration.verdict()` could grade a failing strategy as PASS.**
  `conditions.get(k) is False` is an identity test against the `False`
  singleton, and `np.False_ is False` is `False` — so a condition computed
  the natural way (`t >= 3.0` on numpy or pandas values) could never fail.
  Reproduced on this project's own headline number: t = 2.42 against a bar
  of 3.0 graded as `pass: True`. Conditions are now graded fail-closed:
  numpy/pandas booleans are honoured, anything that is not an unambiguous
  boolean (`None`, `NaN`, `0`, `""`, a float) fails and is named in a new
  `invalid` field, and an array-valued condition raises
  `AmbiguousConditionError` instead of being guessed at.
- **`fill_bracket()` had no axis check** while its sibling
  `block_cluster_eval()` did. Swapping two columns of `fwd` turned a true
  gross of 1.0 into 9.0 with no error — a 9× overstatement produced by the
  module whose purpose is to correct a 1.3–1.5× one. All four frames are now
  required to share exact axes, as is `touch_mask`'s pair.
- **`verdict()` never opened the frozen file.** It graded the in-memory
  document, so lowering the bar after `freeze()` worked. It now reads the
  registration from disk whenever a path is known (`freeze`, `load`, or
  `reg_path=`) and raises `SealBrokenError` when disk and memory disagree,
  when the file was edited, or when it has been deleted. The result carries
  `verified` and `sha256`.
- **The test-look stamp is bound to the registration.** It now records
  `registration_sha256`, `spend_test_look()` refuses to spend a look on a
  registration whose bar has moved, and `seal_status()` reports the whole
  seal (frozen / matches / spent / bound). The seal remains tamper-EVIDENT,
  not tamper-proof — documented in the README rather than implied away.
- **`clustered_t()` no longer pairs mismatched pandas Series positionally.**
  Two Series with different indexes now raise; arrays of different lengths
  now raise.

### Fixed — measurement

- **`expected_max_abs_t()` simulated normals, not t.** Cluster-level t
  statistics have fatter tails, so the reported luck threshold was too low —
  by ~18% at 11 clusters, ~8% at 21, ~3% at 51 — in the flattering
  direction. New `df=` argument (clusters − 1) simulates Student-t; `df=None`
  keeps the normal large-sample limit and is documented as a lower bound.
  The simulation is now chunked, so a 2,000-cell search no longer allocates
  a multi-gigabyte array.
- **The deflation cheat sheet in `docs/workflow.md` disagreed with the
  function it cited** (1.6 / 2.2 / 2.7 versus 1.47 / 2.08 / 2.60). The table
  now quotes the function, carries a t-corrected column, and a test parses
  the table and fails if the two ever diverge again.
- **The null control's required comparison now exists.** The docs insisted
  that a shuffled null converges to the hold baseline rather than to zero,
  and then shipped no hold baseline. New `hold_baseline()` and
  `null_verdict()`: the latter pairs each shuffled run against the hold
  baseline block by block and tests the difference, which is what actually
  detects a pipeline manufacturing its own effect. `ok` is fail-closed — an
  unmeasurable null is not a pass.
- **The ledger can now feed `dsr()`.** `record(..., metrics={"sr": ...})`
  and `TrialLedger.sr_variance()` close the one open loop in the design;
  both shipped demos previously invented the spread. `dsr()` accepts
  `sr_variance=None` and returns `None` (unmeasured), matching its existing
  treatment of an unknown trial count.
- **`prefix_replay_check()` stopped crying wolf.** It compared positionally,
  so any feature that drops warm-up rows raised a broadcast error, and it
  cast to float, so non-numeric features raised too — false alarms on
  correct code, in the tool most likely to be switched off because of them.
  It now aligns on the index and compares non-numeric features by equality.
  New `checked` / `rows_compared` fields and `assert_no_leak()`, because a
  check that compared nothing is not a clean bill of health.

### Fixed — tooling and performance

- `TrialLedger.record()` was O(n²) (re-parsing the whole file per record):
  ~4.1 s for 1,600 trials, now ~0.4 s for 2,000, with cross-instance
  deduplication preserved.
- `lint_source()` truncated at a `#` inside a string literal, so
  `label = "close # then"; x = df.shift(-1)` went unflagged — a false
  negative in a leak detector. It now tokenizes, ignores prose in
  docstrings, accepts directories, honours `# noqa: leak`, and ships a CLI:
  `python3 -m nullbar strategy/` (or `nullbar-lint`), exit 1 on any
  hit.
- `block_cluster_eval()` returns `per_year` on the zero-trade path, so a
  caller no longer `KeyError`s exactly when the strategy took no trades.
- Added `py.typed`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CITATION.cff`. CI now varies pandas explicitly (2.x and 3.x legs) instead
  of relying on pandas' own Python floor to produce the spread the README
  claims.

### Documented

- `psr()`: `kurtosis` is NON-excess (pandas and scipy return excess), and
  the clamped radicand is unreachable for moments computed from real data
  (Pearson's inequality).
- `expected_max_sharpe()` / `expected_max_abs_t()`: both assume independent
  trials; correlated sweeps are over-deflated, which is the safe direction.

## [0.1.0] — 2026-08-12

First extraction from a live production system (crypto spot on Coinbase,
TimescaleDB, 2,100+ tests): registration, ledger, stats, evaluate, fills,
leaklint. Released as `prereg`.
