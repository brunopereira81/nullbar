# Changelog

All notable changes to this project. Dates are UTC.

## [0.6.0] — 2026-08-18

Git anchoring. Steps 1–7 are enforced by your own discipline and your own
clock, which means a study run first and registered afterwards — with a bar
chosen to fit the answer it already knows — produces a flawless report and
an intact seal. This release closes the half of that gap a repository can.

### Added

- **`nullbar anchor <registration.json> [--commit]`** — records which
  commits carry the registration and its test-look stamp into
  `<registration>.anchor.json`. Anchor before you run and again after, and
  the registration's commit is an ancestor of the stamp's; changing that
  order means rewriting history, which changes every descendant hash and,
  once pushed, is a force-push someone else's server saw. Without
  `--commit` it records history rather than making any — a tool that
  commits on your behalf gets to decide what "the record" contains. With
  it, it commits exactly those paths (never your staged work, never
  `--amend`), then commits the anchor record in a following commit, since
  untracked it would be invisible to the clone a reader actually checks.
- **`nullbar verify <registration.json>`** — checks four things against the
  repository now: the committed bytes are the bytes being graded, the
  registration commit precedes the test-look commit, both are still
  reachable from HEAD, and whether any remote-tracking ref contains them.
  Exits non-zero for anything but `intact`, because "not anchored" and
  "anchor holds" must not be the same answer to a CI job. Verification
  resolves the repository from the file it is handed, not from the absolute
  path recorded at anchor time, so a third party clones wherever they like
  and it still checks.
- **The report renders the anchor** as its own section, and a broken anchor
  makes the verdict `CONTRADICTED`: the committed record and the graded
  record disagreeing is the same class of fact as a bar that contradicts its
  own grading. An unanchored record is named as a gap.

### Notes

- The section states its own limits on the page. A git anchor does **not**
  prove wall-clock time — commit dates are self-reported and
  `GIT_COMMITTER_DATE` forges them in one environment variable, so only a
  push to a host the researcher does not control was witnessed, and by a log
  nobody can audit. It does not prove the researcher had not already seen
  the test window: ordering of documents is not ordering of knowledge. And a
  local-only repository proves nothing at all — rewind far enough and the
  anchor record reverts with everything else, leaving nothing to disagree
  with. `tests/test_anchor.py` pins that blind spot with a passing test
  rather than papering over it, and the report says "local only" out loud.
- For time itself, the next rungs are an RFC-3161 timestamp or a
  transparency log; git is the cheapest option, not the strongest.
- 198 tests (was 170), driving a real repository through subprocess rather
  than a mock of git — the claim here is about what git does to history when
  someone rewrites it. Nine mutations checked, all caught, including
  "ancestry never checked" and "remote-tracking refs never consulted".

## [0.5.0] — 2026-08-18

The report. A registration, its ledger and its test-look stamp are three
files that only mean something together; until now the only thing that put
them side by side was a sequence of `print()` calls in an example. This
release renders them as one self-contained HTML page — the artifact you hand
someone who was not in the room.

### Added

- **`nullbar report <registration.json>`** — one HTML file, no external
  assets, no scripts, prints to PDF. Carries the frozen registration
  verbatim with its sha256 (so a reader can re-hash it), the trial count
  against the registered cell budget, the null control, the clustered
  result, the fill bracket, the deflation against its **95th-percentile**
  noise threshold, when the single test look was spent and whether it is
  still bound to this registration, and the bar with each condition's
  observed value beside its verdict. `--json` emits the same facts as data;
  `--ledger` supplies the trial count.
- **Four statuses, and only one is good news**: `PASS`, `FAIL`,
  `INCOMPLETE` (the record does not establish a verdict — explicitly not a
  pass) and `CONTRADICTED` (the frozen bar and the recorded grading
  disagree). `nullbar report` exits non-zero for anything but `PASS`,
  because in CI an incomplete record must not be indistinguishable from a
  passing one.
- **Findings** are raised on the face of the report: a test look not bound
  to this registration (the design moved after the look was spent), a look
  stamped before the registration existed, an unreadable stamp, a search
  that blew its registered cell budget, and a bar that disagrees with its
  own grading.
- **`nullbar.evidence(result, null=…, fills=…, conditions=…, **metrics)`** —
  assembles the test-look payload so the record is complete by construction,
  and derives the metric vocabulary the bar and the report rely on
  (`null_max_abs_t`, `hold_gross`, `touch_gross`, `fill_haircut`, …).
  Explicit metrics always beat derived ones. Prose conditions go through the
  same fail-closed classifier `verdict()` uses, so a `None` stays visible as
  invalid instead of being coerced to a quiet `False`.
- **A `nullbar` console script** with two verbs, `report` and `lint`. Bare
  paths still lint, so `python3 -m nullbar strategy/` and `nullbar-lint` are
  unchanged.
- **Two-sided cell counts.** `|t|` spans both tails, so a signal tested long
  AND short is one cell, and only the researcher knows which ledger rows
  pair up. An `n_cells` recorded on the test look therefore wins over the
  ledger's row count — and because it is the one input a researcher could
  shrink to flatter themselves, a recorded count BELOW the ledger's is
  disclosed on the face of the report rather than silently adopted.

### Fixed

- **Every registration was stored under the name of its last bar
  condition.** The bar-validation loop added in 0.3.0 used `name` as its
  loop variable, shadowing the registration's own `name` parameter, so
  `Registration(name="mean-reversion-24h", bar={..., "beats_hold": ...})`
  froze itself as `beats_hold` — in the file, in its hash, and in the
  test-look stamp. Affects 0.3.0 and 0.4.0. Found by the first report ever
  rendered off a frozen file, which is the argument for the feature in one
  line: the record had been wrong for two releases and no `print()` in any
  example showed it.

- **The fill haircut is only a haircut when there is gross to cut.**
  `touch / assumed` is now computed only for a positive assumed leg: two
  negative legs divide to a healthy-looking 0.97 and a sign change divides
  to -2.39, and both read as fractions that survived. Where the touch leg
  goes negative the report says outright that resting-fill pricing leaves
  no gross to haircut — which is what the flagship study's own record does.

- **`docs/sample-report.html`** — a finished report on the real study
  behind this library: the 24h mean-reversion rule over seven years of
  hourly bars, 32 two-sided cells of search, FAIL on three of four frozen
  conditions.

### Notes

- The report reads the record and never recomputes a result from market
  data — a report that recomputes can quietly report something the
  registration never graded. The only arithmetic at report time is the
  deflation simulation, from recorded counts, seeded and reproducible.
- What is not in the payload is not in the report, and the report says so:
  missing pieces are listed under *What this record does not contain*
  rather than rendered as a blank cell.
- The report inherits the seal's limit and states it on its face: it is
  tamper-evident, not tamper-proof.
- 170 tests (was 113). Every new behaviour mutation-checked: sixteen
  mutations, including "no test look reads as PASS", "the deflation quotes
  the expected maximum instead of the 5% line", and the name-shadowing bug
  itself — all caught.

## [0.4.0] — 2026-08-18

A third audit pass, run against 0.3.0, confirmed every earlier finding fixed
and left four residuals plus two redundant file reads. All are closed here.

### Fixed

- **`expected_max_abs_t` returned a threshold nothing could pass at low
  `df`.** The t tails own the MEAN of max|t| when the cell count is small —
  16 cells read 6.97 at `df=2` against 2.08 for the normal — and
  `clustered_t` permits three clusters, so a user could legitimately land
  there. The mean is now refused below `df=5`, with `summary="median"` (or
  any quantile in (0, 1)) available for anyone who needs a number there and
  is willing to say which number it is. Every published value is unchanged:
  the refusal only covers a region the docs never quoted.
- **The lint no longer flags prose.** `msg = "never use .shift(-1) here"`
  was a hit. Matches are now discarded when they lie ENTIRELY inside a
  string literal, which keeps the other half of the same bug fixed —
  `df["col#1"].shift(-1)` is still caught, and so are the patterns that
  match a string ARGUMENT on purpose (`merge_asof(direction="forward")`,
  `fillna(method="bfill")`). Blanking the literals outright, the obvious
  fix, silently disables those two.
- **A missing path is an error message, not a traceback.**
  `lint_source` raises a plain `FileNotFoundError("no such file or
  directory: …")` and the CLI prints it and exits 2.
- **Resting ASKS are modelled.** `touch_mask`, `through_mask` and
  `fill_bracket` take `side="buy"` (default, a bid measured against the LOW
  frame) or `side="sell"` (an ask against the HIGH), with the through-margin
  moving the correct way for each. The second positional parameter is
  renamed `low` -> `extreme` accordingly.

### Fixed — deflation, from a re-read of the FIRST audit

Its F6 had three parts and only part (a), the cheat sheet disagreeing with
its own function, was ever acted on.

- **The expected maximum is not a bar.** Pure noise beats its own E[max|t|]
  about 45% of the time (measured: 45.4% over best-of-64), so quoting it as
  the threshold to clear is materially too lenient. A level noise reaches 5%
  of the time is the 95th percentile of max|t| — 3.35 at 64 cells against
  2.60 for the mean. The cheat sheet now carries both columns, the docs and
  docstrings say to set bars from the tail, and the docs-vs-function test
  checks both columns.
- **|t| is two-sided, so direction pairs are one cell.** A signal tested
  long AND short is one two-sided cell, not two: 16 signals x 2 horizons x 2
  directions is 32 cells, not 64. Documented, and applied in the walkthrough
  and the launch post.

- **The cheat sheet described its third column twice, and the two
  descriptions disagreed.** The v0.3.0 sentence "column 3 is
  `expected_max_abs_t(k, df=20)`" survived under a table whose third column
  had become the 95th percentile — 3.35 at 64 cells is p95; `df=20` gives
  2.90. The numeric docs-vs-function test passed straight through it,
  because it compared the TABLE to the function and never read the prose.
  There is now a second test: every `expected_max_abs_t(...)` call the
  section names must produce a number the section prints.

### Internal

- `verdict()` read the frozen registration twice (once to grade, once for
  the seal status) and `lint_source` read every file twice. Both read once.

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
