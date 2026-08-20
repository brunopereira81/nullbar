"""Git anchoring: put the record somewhere you cannot quietly rewrite it.

The seal proves what was promised. It cannot prove WHEN, because every
timestamp in the record is written by the researcher's own clock — so a
study run first and registered afterwards, with a bar chosen to fit the
answer, produces a perfect report and an intact seal.

Git fixes the half of that a repository can. Commit the registration, run
the study, commit the test-look stamp: the registration's commit is then an
ANCESTOR of the stamp's, and that ordering cannot be changed without
rewriting history — which changes every descendant hash and, once pushed,
is a force-push someone else's server saw.

What this does prove:
  * the bytes that were committed are the bytes being graded (blob hash);
  * the registration commit precedes the test-look commit (ancestry);
  * both commits are still in the history reachable from HEAD;
  * whether a remote-tracking ref contains them — i.e. whether anyone
    outside this machine ever saw them.

What it does NOT prove, and the report says so:
  * WALL-CLOCK TIME. Commit dates are self-reported and `GIT_COMMITTER_DATE`
    forges them in one environment variable. Only the push was witnessed,
    and only by the host, whose logs are not evidence anyone can audit. For
    real time, anchor the hash with an RFC-3161 timestamp or a transparency
    log instead — this module is the cheapest option, not the strongest.
  * that the researcher had not already seen the test window. Ordering of
    documents is not ordering of knowledge. Nothing short of someone else
    holding the data fixes that one.
  * anything at all, if the repository never leaves the researcher's
    machine: a local history can be rebuilt from scratch in a minute.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._records import (MAX_RECORD_BYTES, RecordReadError, check_record,
                       record_bytes, record_text)

ANCHOR_SUFFIX = ".anchor.json"

#: Roles an anchor covers, and where each lives relative to the
#: registration. The LEDGER is here because the two things a reader must be
#: able to trust are the bar and the number of cells it was set against, and
#: the ledger carries the second. Left out, a ledger can be quietly SHRUNK:
#: the budget check then passes against a search that never happened, and
#: nothing in the record disagrees.
ROLE_SUFFIXES = {"registration": None,
                 "test_look": ".test_look.json",
                 "ledger": ".jsonl"}


def role_paths(reg: Path) -> dict[str, Path]:
    """Where each anchored role lives, for a given registration."""
    return {role: (reg if suffix is None else reg.with_suffix(suffix))
            for role, suffix in ROLE_SUFFIXES.items()}


class GitError(RuntimeError):
    """A git invocation failed, or there is no repository here."""


def _git(args: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), text=True,
                             capture_output=True, check=True)
    except FileNotFoundError as exc:                 # git not installed
        raise GitError("git is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise GitError((exc.stderr or exc.stdout or "").strip()
                       or f"git {' '.join(args)} failed") from exc
    return out.stdout.strip()


def _ok(args: list[str], cwd: Path) -> bool:
    """True if the command exits 0 — for git's predicate commands, where a
    non-zero exit is an answer rather than an error."""
    try:
        subprocess.run(["git", *args], cwd=str(cwd), check=True,
                       capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _toplevel(path: Path) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], path.parent))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob(commit: str, rel: str, repo: Path) -> bytes | None:
    """The committed bytes, or None if that path is not in that commit.

    SIZE-CHECKED FIRST. The working-tree read is guarded, and this one was
    not: a three-line sidecar can name a huge historical blob, and
    ``git cat-file blob`` with ``capture_output=True`` allocates all of it
    before any guard sees a byte. The same unbounded read, arriving from
    the object database instead of the filesystem — the third route into
    one function, after the entry paths and the sidecar itself.
    """
    try:
        size_out = subprocess.run(
            ["git", "cat-file", "-s", f"{commit}:{rel}"],
            cwd=str(repo), capture_output=True, check=True, text=True)
        size = int((size_out.stdout or "0").strip() or 0)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return None
    if size > MAX_RECORD_BYTES:
        raise RecordReadError(
            f"the blob {commit[:12]}…:{rel} is {size} bytes, over the "
            f"{MAX_RECORD_BYTES}-byte limit — it is not being read whole")
    try:
        out = subprocess.run(["git", "cat-file", "blob", f"{commit}:{rel}"],
                             cwd=str(repo), capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out.stdout


def _last_commit(rel: str, repo: Path) -> str | None:
    """The last commit touching ``rel``, or None if there is none.

    A GitError is None, not an exception. ``git log`` FAILS on an unborn
    HEAD — a repository with everything staged and nothing committed yet —
    and that error escaped ``verify_anchor``, whose entire contract is to
    return one of four statuses. It reached ``nullbar report`` as a
    traceback, because GitError is a RuntimeError and that command catches
    ValueError and OSError.

    Mapping it to None is not a shrug: "git cannot name a commit for this
    path" and "there is no commit for this path" are the same fact to
    everything downstream, which already treats None as "not committed".
    """
    try:
        out = _git(["log", "-1", "--format=%H", "--", rel], repo)
    except GitError:
        return None
    return out or None


def _remotes_containing(commit: str, repo: Path) -> list[str]:
    """Remote-tracking refs that contain this commit — the only part of a
    git anchor anyone outside this machine ever saw."""
    try:
        out = _git(["branch", "-r", "--contains", commit], repo)
    except GitError:
        return []
    return sorted(line.strip() for line in out.splitlines()
                  if line.strip() and "->" not in line)


def _entry(path: Path, repo: Path) -> dict[str, Any]:
    # A path that resolves OUT of the repository — a symlink to /dev/zero,
    # say — made `relative_to` raise a bare ValueError from deep inside
    # pathlib, which nothing caught and no message explained. The verifier
    # already refuses this case in its own vocabulary; anchoring should
    # say the same thing rather than crash.
    try:
        rel = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        raise GitError(
            f"{path} resolves outside the repository at {repo} — an anchor "
            "records files in the repository it attests to") from None
    commit = _last_commit(rel, repo)
    if commit is None:
        raise GitError(
            f"{rel} is not in this repository's history — commit it first "
            f"(git add {rel} && git commit), or pass --commit")
    return {"path": rel, "commit": commit,
            "sha256": _sha256(record_bytes(path, "record")),
            "committed_at": _git(["show", "-s", "--format=%cI", commit],
                                 repo)}


def anchor(reg_path: str | Path, *, ledger: str | Path | None = None,
           commit: bool = False, message: str | None = None) -> dict[str, Any]:
    """Record which commits carry the registration and its test-look stamp.

    Writes ``<registration>.anchor.json`` beside them and returns it. By
    default it records history rather than making any — the file must
    already be committed, because a tool that commits on your behalf gets to
    decide what "the record" contains. ``commit=True`` commits exactly these
    paths (never your staged work) with a message naming what it is.
    """
    reg = Path(reg_path)
    if not reg.exists():
        raise FileNotFoundError(f"no registration at {reg}")
    repo = _toplevel(reg)
    paths = role_paths(reg)
    # An explicit ledger beats the sibling convention. Coverage that
    # depends on a filename fails SILENTLY when the name differs — this
    # library's own walkthrough writes `trials.jsonl` beside `mr24.json`,
    # and the first version of this covered nothing at all there while
    # reporting an intact anchor.
    if ledger is not None:
        paths["ledger"] = Path(ledger)
    targets = [p for p in paths.values() if p.exists()]

    if commit:
        _commit(repo, targets, message or f"anchor: {reg.name}")

    doc: dict[str, Any] = {
        "kind": "git",
        "anchored_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "remotes": dict(_remote_pairs(repo)),
        "entries": {"registration": _entry(reg, repo)},
    }
    uncovered: list[str] = []
    for role in ("test_look", "ledger"):
        path = paths[role]
        if not path.exists():
            continue
        try:
            doc["entries"][role] = _entry(path, repo)
        except GitError as exc:
            # A file that exists but is not committed cannot be anchored,
            # and refusing the whole anchor over it would leave the
            # registration unanchored too. Record the hole instead of
            # skipping quietly: verification reads this back as a finding,
            # so "not covered" can never be mistaken for "nothing to cover".
            if role == "test_look":
                raise
            uncovered.append(f"{role}: {exc}")
    if uncovered:
        doc["uncovered"] = uncovered
    out = reg.with_suffix(ANCHOR_SUFFIX)
    out.write_text(json.dumps(doc, indent=2))
    if commit:
        # The sidecar goes in its own, later commit — it names the hashes of
        # the commits above, so it cannot be inside them. That is fine: a
        # verifier recomputes every claim from git, so the sidecar only has
        # to be PRESENT in the checkout. Untracked, a clone has nothing to
        # check and the whole anchor is invisible to the third party it
        # exists for.
        _commit(repo, [out], f"anchor: record {reg.name}")
    return doc


def _commit(repo: Path, paths: list[Path], message: str) -> bool:
    """Commit exactly these paths. Never `git commit -a`, never `--amend`:
    the first would sweep in whatever else is in the tree, and the second
    rewrites the commit an earlier anchor may already point at."""
    rels = [p.resolve().relative_to(repo.resolve()).as_posix() for p in paths]
    _git(["add", "--", *rels], repo)
    if _ok(["diff", "--cached", "--quiet", "--", *rels], repo):
        return False                    # already committed, unchanged
    _git(["commit", "-m", message, "--", *rels], repo)
    return True


def _remote_pairs(repo: Path) -> list[tuple[str, str]]:
    pairs = []
    for line in _git(["remote", "-v"], repo).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == "(fetch)":
            pairs.append((parts[0], parts[1]))
    return pairs


def _regular(path: Path) -> bool:
    """True only for an ordinary file we may safely read whole.

    ``exists()`` is true of a character device, and ``read_bytes()`` on
    ``/dev/zero`` returns an infinite stream: it allocates until the machine
    dies. That is not hypothetical — a mutation test that disabled
    ``_inside`` below drove exactly this path and took the box down three
    times, killing the editor session with it, because the scope those
    processes shared has ``OOMPolicy=stop``.

    So containment is not the only guard. ``_inside`` keeps a crafted path
    out of the checkout; this keeps an unbounded read from happening at all,
    including for a device or FIFO sitting INSIDE the repository, where
    containment has nothing to say. A guard that can be regressed should not
    be the only thing standing between a bad record and the machine.
    """
    try:
        return path.is_file()          # follows symlinks; False for dev/fifo
    except OSError:
        return False


def _inside(rel: str, repo: Path) -> bool:
    """Is ``rel`` a relative path that stays inside ``repo``?

    ``repo / rel`` is not a containment check: an ABSOLUTE rel discards
    ``repo`` entirely, and ``../`` walks out of it. Verification then reads
    whatever the sidecar names — a file outside the repository, or a special
    file with no end to it — before returning any verdict, on a record that
    by construction comes from somewhere untrusted. Refuse the path instead
    of resolving it; nothing here needs to leave the checkout.
    """
    try:
        candidate = Path(rel)
        # A fast path, deliberately REDUNDANT: the resolve-and-compare below
        # already rejects both of these. It is kept because it states the
        # intent at the top and refuses hostile input without a syscall —
        # and it is recorded as redundant here because a mutation removing
        # it survives the suite, correctly, and a surviving mutant that
        # nobody explains is indistinguishable from a missing test.
        if candidate.is_absolute() or ".." in candidate.parts:
            return False
        base = repo.resolve()
        return (base / candidate).resolve().is_relative_to(base)
    except (TypeError, ValueError, OSError):
        return False


def verify_anchor(reg_path: str | Path) -> dict[str, Any]:
    """Check a recorded anchor against the repository, now.

    ``status`` is one of ``unanchored`` (no sidecar), ``unverifiable`` (no
    git, no repo, or an unreadable sidecar — never treated as a pass),
    ``broken`` (the committed bytes, the commits themselves, or the ordering
    do not hold) and ``intact``.
    """
    reg = Path(reg_path)
    side = reg.with_suffix(ANCHOR_SUFFIX)
    out: dict[str, Any] = {"status": "unanchored", "path": str(side),
                           "entries": {}, "findings": [], "notes": [],
                           "ordering": None, "witnessed": False}
    if not side.exists():
        return out          # an existence test opens nothing

    # ORDER MATTERS, and it did not before: the sidecar was parsed first and
    # the repository resolved afterwards, so every guard below applied to
    # paths this function DERIVED and none of them to the path it was
    # HANDED. A tracked symlink named `*.anchor.json` pointing at /dev/zero
    # was therefore read whole — the same unbounded read that had just been
    # fixed one layer in, arriving through the front door. Resolve the
    # checkout, prove the sidecar is an ordinary file inside it, and only
    # then read it.
    #
    # The repository is the one holding the file under inspection, NOT the
    # absolute path recorded at anchor time. A third party verifies by
    # cloning — to a path of their choosing — and the recorded path is
    # provenance, not a lookup key. Resolving it the other way round makes
    # the check pass or fail on where someone happened to put the clone.
    try:
        repo = _toplevel(reg)
    except GitError as exc:
        out["status"] = "unverifiable"
        out["findings"].append(
            f"{reg} is not inside a git repository here ({exc}) — an "
            "anchor can only be checked from a checkout that carries it")
        return out

    try:
        rel = side.resolve().relative_to(repo.resolve())
    except (ValueError, OSError):
        out["status"] = "unverifiable"
        out["findings"].append(
            f"the anchor sidecar {side} resolves outside the checkout at "
            f"{repo} — an anchor is a record in the repository it attests "
            "to, and nullbar will not follow it out of one")
        return out
    del rel

    try:
        doc = json.loads(record_text(side, "anchor sidecar"))
        entries = doc["entries"]
        out["anchored_in"] = doc.get("repo")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        out["status"] = "unverifiable"
        out["findings"].append(f"the anchor sidecar is unreadable ({exc})")
        return out

    if isinstance(entries, dict):
        # Every entry is dereferenced with .get() below. The empty-mapping
        # fix asked whether entries EXIST; it never asked what shape they
        # are, so {"registration": []} raised AttributeError out of the
        # verification path — and out of report generation with it. A
        # record whose entries cannot be read is unverifiable, which is a
        # verdict; a traceback is not.
        malformed = [role for role, rec in entries.items()
                     if not isinstance(rec, dict)
                     or not isinstance(rec.get("commit"), str)
                     or not isinstance(rec.get("path"), str)
                     or not _inside(rec["path"], repo)]
        if malformed:
            out["status"] = "unverifiable"
            out["findings"].append(
                "the anchor record is malformed: "
                + ", ".join(f"the {role} entry does not name a commit and a "
                            "path inside this repository"
                            for role in sorted(malformed)))
            return out

    if not isinstance(entries, dict) or "registration" not in entries:
        # "intact" was returned for a sidecar naming nothing at all: the
        # loop below simply had no work, so every check passed vacuously.
        # An anchor that does not anchor the registration is unverifiable,
        # not verified.
        out["status"] = "unverifiable"
        out["findings"].append(
            "the anchor record names no registration entry — it attests to "
            "nothing, and an empty attestation is not an intact one")
        return out

    broken = False
    paths = role_paths(reg)
    for role, rec in entries.items():
        commit, rel = rec.get("commit", ""), rec.get("path", "")
        # A refusal is a VERDICT — "we would not read that" — and must not
        # escape as a traceback out of `nullbar verify` or report
        # generation. The guards were added inside this loop without
        # anything here to catch them, so a record that tripped one crashed
        # instead of reading `broken`.
        try:
            blob = _blob(commit, rel, repo)
            # Resolved from the path the anchor RECORDS, not from a filename
            # convention — the ledger may be named anything, and an unknown
            # role must be checked rather than waved through. The
            # registration is the exception: it is the file the caller
            # handed us, and verifying a different one would answer a
            # question nobody asked.
            disk = reg if role == "registration" else (repo / rel)
            readable = _regular(disk)
            disk_bytes = record_bytes(disk, role) if readable else None
        except RecordReadError as exc:
            broken = True
            out["findings"].append(f"the {role} could not be read: {exc}")
            out["entries"][role] = {"path": rel, "commit": commit,
                                    "refused": str(exc)}
            continue
        on_disk = _sha256(disk_bytes) if disk_bytes is not None else None
        if disk.exists() and not readable:
            broken = True
            out["findings"].append(
                f"the {role} path {rel} is not an ordinary file — an anchor "
                "names records, and reading whatever else it points at is "
                "how a record takes the machine with it")
        state = {
            "path": rel, "disk_path": str(disk), "commit": commit,
            "present": blob is not None,
            "committed_sha256": _sha256(blob) if blob is not None else None,
            "recorded_sha256": rec.get("sha256"),
            "disk_sha256": on_disk,
            "committed_at": rec.get("committed_at"),
            "in_history": _ok(["merge-base", "--is-ancestor", commit,
                               "HEAD"], repo),
            "remotes": _remotes_containing(commit, repo),
        }
        if blob is None:
            broken = True
            out["findings"].append(
                f"the {role} commit {commit[:12]}… no longer contains "
                f"{rel} — history was rewritten")
        elif on_disk is None:
            broken = True
            out["findings"].append(
                f"the {role} was anchored in {commit[:12]}… but {rel} is no "
                "longer on disk")
        elif role == "ledger" and not disk_bytes.startswith(blob):
            # The ledger is append-only BY DESIGN — "if a trial was run, it
            # counts" — so demanding byte equality would break the moment
            # another cell is recorded, and the check would be turned off.
            # Prefix containment is the invariant that actually holds: rows
            # may be added, never edited or removed. That is precisely the
            # tamper this entry exists to catch, since shrinking a ledger
            # shrinks the deflation every figure downstream is divided by.
            broken = True
            out["findings"].append(
                "the trial ledger on disk is not an extension of the one "
                f"committed in {commit[:12]}… — an append-only record was "
                "rewritten, so trials were edited or removed")
        elif role != "ledger" and state["committed_sha256"] != on_disk:
            broken = True
            out["findings"].append(
                f"the {role} on disk is not the {role} that was committed "
                f"in {commit[:12]}…")
        elif (isinstance(state["recorded_sha256"], str)
              and state["recorded_sha256"] != state["committed_sha256"]):
            # The sidecar names a commit AND the hash of what that commit
            # was supposed to hold. Re-pointing an entry at a different
            # commit — an older ledger, say — leaves the two disagreeing.
            broken = True
            out["findings"].append(
                f"the {role} entry names commit {commit[:12]}… but records "
                "a different sha256 than that commit holds — the anchor was "
                "edited to point somewhere else")
        elif not state["in_history"]:
            broken = True
            out["findings"].append(
                f"the {role} commit {commit[:12]}… is no longer reachable "
                "from HEAD — the branch it was on was rewritten or dropped")
        if state["remotes"]:
            out["witnessed"] = True
        out["entries"][role] = state

    reg_c = (entries.get("registration") or {}).get("commit")
    look_c = (entries.get("test_look") or {}).get("commit")
    if reg_c and look_c:
        distinct = reg_c != look_c
        precedes = distinct and _ok(
            ["merge-base", "--is-ancestor", reg_c, look_c], repo)
        out["ordering"] = {"registration": reg_c, "test_look": look_c,
                           "distinct": distinct, "precedes": precedes}
        if not distinct:
            out["notes"].append(
                "the registration and the test look were committed in the "
                "SAME commit, so the record carries no evidence that the "
                "bar was set before the result existed")
        elif not precedes:
            broken = True
            out["findings"].append(
                "the registration commit does not precede the test-look "
                "commit — the bar cannot be shown to have been set first")
    elif reg_c:
        out["notes"].append("no test look is anchored yet")

    # ── the trial ledger ────────────────────────────────────────────────
    for hole in doc.get("uncovered") or []:
        out["findings"].append(
            f"the anchor could not cover the {hole} — that part of the "
            "record is attested by nothing")
    ledger = paths["ledger"]
    if "ledger" not in entries and ledger.exists():
        # NOT broken: every record anchored before the ledger was covered
        # would read as tampered, which is false and would teach a reader to
        # ignore the word. It is a hole, and a hole gets said out loud.
        out["notes"].append(
            "the trial ledger is not covered by this anchor: the number of "
            "cells the search spent is attested by nothing, and a ledger "
            "can be shrunk without contradicting any other part of the "
            "record (re-run `nullbar anchor` to cover it)")

    # ── the sidecar itself ──────────────────────────────────────────────
    side_rel = side.resolve().relative_to(repo.resolve()).as_posix()
    # TRACKED is not COMMITTED. `git ls-files` counts a staged file, and a
    # freshly `git add`ed sidecar has no commit touching it yet — so
    # `_last_commit` returned None, the self-check below was skipped, and
    # the record read `intact` with nothing said, while a clone would carry
    # no anchor at all. Both conditions now say the same thing, because
    # they mean the same thing to the reader.
    side_commit = (_last_commit(side_rel, repo)
                   if _ok(["ls-files", "--error-unmatch", "--", side_rel],
                          repo) else None)
    if side_commit is None:
        out["notes"].append(
            "the anchor record itself is not committed, so a clone of this "
            "repository carries no anchor to check")
    else:
        # Guarded like the loop above, and for the same reason: a refusal is
        # a verdict, not a traceback. The loop got its `except` and this did
        # not — the instance fixed, the class not — and an oversized sidecar
        # blob crashed `nullbar verify` from three lines below the fix.
        try:
            side_blob = _blob(side_commit, side_rel, repo)
            side_disk = (record_bytes(side, "anchor record")
                         if side_blob is not None else None)
        except RecordReadError as exc:
            broken = True
            out["findings"].append(
                f"the anchor record itself could not be read: {exc}")
            side_blob = side_disk = None
        if side_blob is not None and side_blob != side_disk:
            # Every claim above was read out of the working-tree sidecar,
            # which is the one file an editor can reach. Re-anchoring
            # legitimately rewrites it — but re-anchoring only ADDS roles or
            # moves one FORWARD to a newer commit. An entry pointed at a
            # commit that is not a descendant of the committed one is the
            # last step of the only tamper the checks above cannot see:
            # shrink the ledger, then aim the entry at a commit where it was
            # already that short.
            try:
                was = (json.loads(side_blob.decode()).get("entries") or {})
            except (ValueError, UnicodeDecodeError):
                was = {}
            for role, before in was.items():
                now = entries.get(role) if isinstance(entries, dict) else None
                if not isinstance(before, dict):
                    # The COMMITTED copy's entry is unreadable, so a move
                    # cannot be detected for this role. Silence here would
                    # be the same shape as every other defect in this file:
                    # the check does not run and nothing says so, which
                    # reads exactly like the check passing.
                    out["notes"].append(
                        f"the committed anchor record's {role} entry is not "
                        "readable, so a move of that entry cannot be "
                        "detected from here")
                    continue
                if not isinstance(now, dict):
                    # DELETION. An entry the committed sidecar attests to,
                    # gone from the working copy, is the cheapest tamper of
                    # all: the loop above simply has no work for that role,
                    # and every check on it passes by not running. Removing
                    # `test_look` also erased the ordering evidence and the
                    # record still read intact, PASS, no findings.
                    #
                    # This is the fourth time the same shape has shipped —
                    # an empty bar, a missing ledger, an empty entries
                    # mapping, and now a deleted entry. Absence is not
                    # innocence, and a check that does not run is not a
                    # check that passed.
                    broken = True
                    out["findings"].append(
                        f"the {role} entry was REMOVED from the anchor on "
                        f"disk — it is attested by the committed record "
                        f"({side_commit[:12]}…) and every check on it was "
                        "skipped rather than performed")
                    continue
                a, b = before.get("commit"), now.get("commit")
                if isinstance(a, str) and isinstance(b, str) and a != b \
                        and not _ok(["merge-base", "--is-ancestor", a, b],
                                    repo):
                    broken = True
                    out["findings"].append(
                        f"the {role} entry was moved from commit {a[:12]}… "
                        f"to {b[:12]}…, which is not a descendant of it — "
                        "the anchor on disk points somewhere the committed "
                        "anchor did not")
            # A note, not a finding: re-anchoring legitimately rewrites the
            # sidecar before it is committed, so this fires in normal use.
            # It is worth saying anyway, because AFTER committing, a
            # difference means the record was edited — and every claim on
            # this page is read out of the file that differs.
            out["notes"].append(
                "the anchor record on disk differs from the copy committed "
                f"in {side_commit[:12]}… — expected while re-anchoring and "
                "before committing; afterwards it means the record was "
                "edited, and the entries checked above were read from the "
                "edited file")
    if not out["witnessed"]:
        out["notes"].append(
            "no remote-tracking ref contains these commits: nothing outside "
            "this machine has seen them, and a local history can be rebuilt")
    out["status"] = "broken" if broken else "intact"
    return out
