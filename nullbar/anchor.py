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
    """The committed bytes, or None if that path is not in that commit."""
    try:
        out = subprocess.run(["git", "cat-file", "blob", f"{commit}:{rel}"],
                             cwd=str(repo), capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out.stdout


def _last_commit(rel: str, repo: Path) -> str | None:
    out = _git(["log", "-1", "--format=%H", "--", rel], repo)
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
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    commit = _last_commit(rel, repo)
    if commit is None:
        raise GitError(
            f"{rel} is not in this repository's history — commit it first "
            f"(git add {rel} && git commit), or pass --commit")
    return {"path": rel, "commit": commit,
            "sha256": _sha256(path.read_bytes()),
            "committed_at": _git(["show", "-s", "--format=%cI", commit],
                                 repo)}


def anchor(reg_path: str | Path, *, commit: bool = False,
           message: str | None = None) -> dict[str, Any]:
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
        return out
    try:
        doc = json.loads(side.read_text())
        entries = doc["entries"]
        out["anchored_in"] = doc.get("repo")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        out["status"] = "unverifiable"
        out["findings"].append(f"the anchor sidecar is unreadable ({exc})")
        return out
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
                     or not isinstance(rec.get("path"), str)]
        if malformed:
            out["status"] = "unverifiable"
            out["findings"].append(
                "the anchor record is malformed: "
                + ", ".join(f"the {role} entry does not name a commit and a "
                            "path" for role in sorted(malformed)))
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
        blob = _blob(commit, rel, repo)
        # An unknown role is resolved from the path the anchor itself names
        # rather than skipped, so a record carrying more than these three
        # roles is checked rather than waved through.
        disk = paths.get(role) or (repo / rel)
        disk_bytes = disk.read_bytes() if disk.exists() else None
        on_disk = _sha256(disk_bytes) if disk_bytes is not None else None
        state = {
            "path": rel, "commit": commit,
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
    if not _ok(["ls-files", "--error-unmatch", "--", side_rel], repo):
        out["notes"].append(
            "the anchor record itself is not committed, so a clone of this "
            "repository carries no anchor to check")
    else:
        side_commit = _last_commit(side_rel, repo)
        side_blob = _blob(side_commit, side_rel, repo) if side_commit else None
        if side_blob is not None and side_blob != side.read_bytes():
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
                if not isinstance(before, dict) or not isinstance(now, dict):
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
