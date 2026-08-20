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
    stamp = reg.with_suffix(".test_look.json")
    targets = [reg] + ([stamp] if stamp.exists() else [])

    if commit:
        _commit(repo, targets, message or f"anchor: {reg.name}")

    doc: dict[str, Any] = {
        "kind": "git",
        "anchored_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "remotes": dict(_remote_pairs(repo)),
        "entries": {"registration": _entry(reg, repo)},
    }
    if stamp.exists():
        doc["entries"]["test_look"] = _entry(stamp, repo)
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
    for role, rec in entries.items():
        commit, rel = rec.get("commit", ""), rec.get("path", "")
        blob = _blob(commit, rel, repo)
        disk = (reg if role == "registration"
                else reg.with_suffix(".test_look.json"))
        on_disk = _sha256(disk.read_bytes()) if disk.exists() else None
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
        elif state["committed_sha256"] != on_disk:
            broken = True
            out["findings"].append(
                f"the {role} on disk is not the {role} that was committed "
                f"in {commit[:12]}…")
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

    if not _ok(["ls-files", "--error-unmatch", "--",
                side.resolve().relative_to(repo.resolve()).as_posix()], repo):
        out["notes"].append(
            "the anchor record itself is not committed, so a clone of this "
            "repository carries no anchor to check")
    if not out["witnessed"]:
        out["notes"].append(
            "no remote-tracking ref contains these commits: nothing outside "
            "this machine has seen them, and a local history can be rebuilt")
    out["status"] = "broken" if broken else "intact"
    return out
