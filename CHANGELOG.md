# Changelog

All notable changes to this project. Dates are UTC.

## [0.7.1] — 2026-08-20

Three findings on the 0.7.0 tag, plus one this repo found by taking its own
machine down with it.

### Fixed

- **`verify_anchor` raised instead of returning a status.** Its whole
  contract is one of four verdicts, and `git log` FAILS on an unborn HEAD —
  a repository with everything staged and nothing committed — so a GitError
  escaped it and reached `nullbar report` as a traceback, because GitError
  is a `RuntimeError` and that command catches `ValueError` and `OSError`.
  Fixed at the source (`_last_commit` maps a git failure to None, which is
  what everything downstream already means by "not committed") rather than
  at each caller: `verify` had been given its own catch and `report` had
  not, which is the same fix-the-instance failure as the entries before the
  sidecar and the loop before the self-check. `nullbar report` catches the
  RuntimeErrors too, as a backstop.
- **A dangling symlink recursed until the stack ran out.** `exists()`
  FOLLOWS the link and is False, so control reached the exclusive create —
  which fails, because the link itself is very much there — and the retry
  found the same state and called `freeze()` again. Neither "it exists" nor
  "it does not" is a usable answer about such a path, so it is refused by
  name, and the retry for a genuine race is bounded rather than recursive.
  (Introduced by the exclusive-creation fix below, in the same release.)
- **`anchor --commit` crashed on a ledger outside the repository.**
  `_commit` computes the same repo-relative paths as `_entry` and did it
  unchecked — and it runs FIRST under `--commit`, so an external `--ledger`
  reached `git add` and raised a bare `ValueError` out of pathlib *ahead of
  the containment guard written for exactly that case*. One implementation
  now, applied to every target before anything is committed; nothing is
  committed when a target is refused. (Also introduced in this release, by
  putting the guard in only one of the two places that needed it.)
- **`freeze()` could lose the refusal it exists to make.** `exists()`-then-
  write is two steps, so two callers freezing DIFFERENT designs at one path
  could both find nothing and both write, the second silently overwriting
  the first. Exclusive creation, and the loser re-enters the existing
  branch — an identical promise is accepted, a different one refused, which
  is the answer either caller would have got had it arrived second.
- **The new exceptions were not importable.** `RecordReadError` and
  `UnlockablePlatformError` are both introduced here and both named in this
  changelog, and `except nullbar.RecordReadError` raised `AttributeError`:
  the only way in was a private module. Exported, with a test that every
  name in `__all__` resolves.
- **A junk entry in the COMMITTED sidecar went silent.** Move-detection
  compares the working record against its committed copy; when that copy's
  entry is unreadable the comparison cannot run, and skipping without
  saying so reads exactly like the check passing.
- **A staged sidecar reported `intact` with nothing said.** `git ls-files`
  counts a STAGED file, so a freshly `git add`ed anchor record passed the
  tracked check while `_last_commit` returned None — the self-check was
  skipped and the record read clean, although a clone would carry no anchor
  at all. Tracked and committed are now the same question, because they mean
  the same thing to a reader.
- **The CLI answers instead of crashing.** `nullbar anchor` caught
  `FileNotFoundError` and `GitError` and nothing else, so an oversized or
  non-regular registration produced a traceback where a printed refusal
  belonged — the same "a verdict escaped as a crash" the verifier had.
  `nullbar verify` caught nothing whatsoever, and git can still fail
  underneath it. Both print and exit 2 now. A third case surfaced while
  testing it: `anchor()` on a path resolving OUT of the repository raised a
  bare `ValueError` from inside pathlib, which no message explained; it
  refuses in the verifier's own vocabulary now.
- **The lock opened the path before anything checked it.** The ledger's
  guard sat in ``_read_rows``, one step too late: acquiring the lock had
  already run ``touch()`` and ``open("r+")`` on whatever the path pointed
  at. On a world-writable ``/dev/zero`` that happens to succeed and the
  refusal arrives correctly — on a stricter box it raises ``PermissionError``
  from the lock setup instead. A guard whose answer depends on the
  permissions of a device node is not a guard. An existing path is validated
  before the lock touches it; a ledger that does not exist yet is still
  created.
- **A committed blob was exempt from the size cap.** The working-tree read
  was guarded and ``git cat-file blob`` was not, so a three-line sidecar
  could name a huge historical blob and verification allocated all of it
  before any guard saw a byte — the same unbounded read arriving from the
  object database rather than the filesystem, the third route into one
  function. The size is queried first (``git cat-file -s``) and refused
  against the same cap.
- **A refusal escaped as a traceback.** Guards were added inside the
  verification loop with nothing there to catch them, so a record that
  tripped one crashed ``nullbar verify`` and report generation instead of
  reading ``broken``. Caught now — in the loop *and* in the sidecar
  self-check below it, which the first version of this fix missed: the
  instance fixed, the class not, which is the shape of nearly every entry
  above it.
- **The sidecar itself was read before any guard applied to it.** The
  previous entry hardened the paths `verify_anchor` *derives* and left the
  path it is *handed* wide open: the anchor record was parsed before the
  repository was even resolved, so a tracked symlink named `*.anchor.json`
  pointing at `/dev/zero` was read whole — the same unbounded read, one
  layer out, through the front door. The checkout is resolved first now, the
  sidecar must be an ordinary file whose resolved path stays inside it, and
  only then is it parsed. "No anchor at all" still reads `unanchored` rather
  than `unverifiable`, because those are different answers.
- **Every record read is guarded, not just the anchor's.** The same hole was
  open in nine other places — the registration, the test-look stamp and the
  trial ledger are all just paths in a tree somebody else may have written,
  and a ledger symlinked at an endless stream loops forever building lines
  rather than reading one whole. `nullbar/_records.py` is now the single
  reader: an ordinary file, under a generous size cap, or `RecordReadError`
  (an `OSError`, so existing handlers degrade instead of crashing). It is
  one function rather than a check per call site *because* the first version
  was a check per call site and it missed the front door.
- **The lock guarantee is no longer dropped silently off POSIX.** Without
  `fcntl` the ledger quietly ran unlocked, restoring the very defect the
  lock was written to close on a platform the docs never excluded. Windows
  now takes an `msvcrt` byte-range lock (exclusive for readers too — it has
  no shared mode: slower, never wrong). Where neither primitive exists,
  `TrialLedger(...)` raises `UnlockablePlatformError` unless the caller
  passes `require_lock=False`, which puts the accepted weakening in their
  code rather than in ours. CI is ubuntu-only, so the Windows branch is
  covered by driving the selection, not the platform — stated here because
  "tested" and "implemented" are different words.
- **Deleting an anchored entry still verified as intact.** The sidecar was
  compared against its committed copy, but an entry present in the commit
  and *absent* from the working file was skipped rather than rejected —
  so removing `test_look` erased the ordering evidence and the record still
  read `intact`, `PASS`, no findings, and `nullbar verify` still exited 0.
  A removed entry is now `broken`. This is the fourth defect of one shape
  in a week — an empty bar, a missing ledger, an empty `entries` mapping,
  and now a deleted entry — and the shape is that **absence read as
  innocence**: a check that does not run looks exactly like a check that
  passed.
- **Ledger deduplication was racy.** `record()` scanned, decided and
  appended in three steps with two gaps in them, so two workers recording
  the same `(name, params)` both saw no row and both wrote it — breaking
  the documented "an identical pair is one trial" guarantee and inflating
  the count every deflation figure divides by. Read, decide and append now
  happen under one exclusive `flock`, with a forced re-read inside it
  (the size-based cache is a proxy, and two rows can serialise to the same
  length). Verified with 32 concurrent processes: an identical pair
  collapses to one row, and 32 distinct pairs all survive.
- **Verification read paths outside the repository.** `repo / rel` is not a
  containment check: an absolute `rel` discards `repo` and `../` walks out
  of it, so a crafted sidecar could make verification read arbitrary files
  before returning any verdict. Every entry path must now be a relative
  path that resolves inside the checkout, refused *before* anything is
  opened.
- **An unbounded read can no longer happen at all.** `exists()` is true of
  a character device and `read_bytes()` on `/dev/zero` allocates until the
  machine dies. Entries must now point at ordinary files. This is defence
  in depth rather than a second containment check — it covers a device or
  FIFO *inside* the repository, where containment has nothing to say, and
  it holds when the path guard is regressed. It was found the hard way: a
  mutation test that disabled the path guard drove exactly this path and
  killed the machine three times, taking the editor session with it.

## [0.7.0] — 2026-08-20

Eight ways a green PASS could be produced without the evidence to support
it, or the check meant to catch it could fail to run at all. Each was
reproduced before being fixed and mutation-checked after.

### Added

- **The anchor covers the trial ledger.** The two things a reader has to be
  able to trust are the bar and the number of cells it was set against, and
  only the first was attested. A ledger left outside the anchor can be
  quietly *shrunk*: the budget check then passes against a search that never
  happened, every deflation figure is divided by a smaller number, and
  nothing else in the record disagrees — demonstrated by replacing a 2-row
  ledger with one fabricated line and watching the anchor report `intact`
  and the verdict stay `PASS`. Because the ledger is append-only by design
  ("if a trial was run, it counts"), it is checked as a **prefix** rather
  than for byte equality — demanding equality would break the moment another
  cell is recorded, and a check that breaks gets turned off. Recording a
  further trial keeps the anchor intact; rewriting or removing one breaks
  it. Two further tampers now break it as well: an entry whose recorded
  hash disagrees with the commit it names, and an entry moved to a commit
  that is not a descendant of the one the *committed* sidecar named — which
  is the last step of the only chain the per-entry checks cannot see.
  Records anchored before this still read `intact`, with the uncovered
  ledger stated as a note: marking every older record as tampered would
  teach a reader to ignore the word.

  `anchor()` and `nullbar anchor` take `--ledger` for a ledger that is not
  the registration's own stem — **coverage that depends on a filename fails
  silently when the name differs**, and the first version of this covered
  nothing at all in nullbar's own walkthrough (which writes `trials.jsonl`
  beside `mr24.json`) while still reporting an intact anchor. The report
  closes that loop from the other side: it is the one place that knows both
  which ledger the count came from and which files the anchor covers, and it
  names the ledger when they disagree.

### Upgrade note

Anchor records written by this version carry a third entry, `ledger`.
**Verify them with 0.7.0 or later.** nullbar 0.6.0 resolved an entry's file
from a hardcoded role -> path mapping rather than from the path the record
names, so it reads the ledger entry against the test-look stamp, finds the
hashes disagree, and reports `broken` — the strongest accusation the tool
makes, on a record that is fine. Caught on this repo's own dashboard, which
had 0.6.0 resident in memory and turned seven intact records red. Entries
are resolved from the recorded path now, so a future role added to the
format will not do this again.

### Fixed — the one look, the budget, and malformed records

- **The one-look guard had a race.** `spend_test_look` asked whether the
  stamp existed and then wrote it, in two steps. Two callers could both
  find no stamp and both succeed — forcing the interleaving produced two
  looks, and neither caller could tell. Creation is now exclusive
  (`open("x")`), so the question and the answer are one operation and the
  kernel arbitrates. "The held-out test is one look" is the single promise
  this library exists to keep.
- **An unusable cell budget passed or crashed.** `cells_budget` was never
  validated: `0` — a search that evaluated nothing — reported a clean PASS,
  because there was nothing to deflate and no gap to block; a non-numeric
  value instead raised `ValueError` out of `int()` far from the cause. A
  budget must now be a whole number of at least one, and a record already
  on disk carrying something else reads **INCOMPLETE** with the reason
  named, rather than being read as a budget of one.
- **A malformed anchor entry crashed verification.** Rejecting an empty
  `entries` mapping asked whether entries exist, never what shape they are,
  and every entry is dereferenced with `.get()` — so
  `{"entries": {"registration": []}}` raised `AttributeError` out of
  verification, and out of report generation with it. An entry that does
  not name a commit and a path makes the record **unverifiable**, which is
  a verdict; a traceback is not.

### Fixed — five ways a PASS could be green without its evidence

- **A bar changed after the test look reported PASS.** Lowering a frozen
  threshold once the result is known is precisely the attack the seal exists
  to catch, and it was recorded as a *finding* while the status stayed green
  — a stamp naming a different sha256 than the file it grades now reads
  **CONTRADICTED**, as a broken anchor already did. The anchor is optional;
  the seal is not, so the seal was the more serious of the two to have left
  advisory.
- **A claimed search with no trial count reported PASS.** Gaps were
  collected and then ignored by the status. A registration claiming more
  than one cell with no ledger now reads **INCOMPLETE**: the deflation its
  bar was set against cannot be computed at all. Gaps that do not bear on
  the verdict still do not block it — a single-cell registration needs no
  ledger, and requiring an anchor would make PASS unreachable without git.
  The README promised the stronger rule and now states the real one.
- **An empty bar was accepted and passed.** `Registration(bar={})` graded
  every result as PASS with zero conditions. Refused at construction: a
  promise with nothing to satisfy is not a promise. Refusing at *freeze*
  only closes the door going forward, though — a record frozen by an older
  version, or written by hand, still loads, and "no condition failed" is
  vacuously true of a promise that registered nothing, which graded a
  result whose only metric was `t = -99` as a clean pass. Such a record
  still loads, because refusing would mean nobody could inspect the record
  most in need of inspection; it can no longer pass, and reads
  **INCOMPLETE** — the record does not say.
- **A malformed anchor verified as intact.** A sidecar containing
  `{"entries": {}}` returned `intact` because the checking loop had no work
  and every check passed vacuously. An anchor that does not name a
  registration is **unverifiable**, not verified.
- **The trial count silently undercounted.** `record()` hashed
  `{"name": name, **params}`, so a `params["name"]` overwrote the strategy
  name and two different strategies sharing that parameter deduplicated into
  one trial — shrinking the very number every deflation figure depends on.
  Hashed nested now, and dedupe compares the semantic pair as well as the
  stored hash so an older ledger still matches instead of double-counting.

### Notes

- 209 tests (was 199). Six mutations checked, all caught.
- Consumers reading a report without its ledger will see INCOMPLETE where
  they saw PASS. That is the fix working: pass the ledger.

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
