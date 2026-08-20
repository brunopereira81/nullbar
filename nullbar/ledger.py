"""Trial ledger — the number nobody wants to remember.

Every deflated statistic needs the TOTAL count of variants ever evaluated in
the search, including the embarrassing ones. Human memory reliably reports
"just this one idea"; the ledger is append-only JSONL that reports the truth.

Record the METRICS with each trial (``metrics={"sr": ...}``) and the ledger
can also supply the OTHER number deflation needs — the spread of Sharpes
across the search — instead of leaving you to invent it.

There is deliberately NO delete API. If a trial was run, it counts.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from ._records import RecordReadError, check_record, loads_mapping

try:                                   # POSIX
    import fcntl
except ImportError:                    # pragma: no cover - platform dependent
    fcntl = None                       # type: ignore[assignment]
try:                                   # Windows
    import msvcrt
except ImportError:                    # pragma: no cover - platform dependent
    msvcrt = None                      # type: ignore[assignment]

#: Is there any cross-process lock on this platform? The first version of
#: the dedup fix answered "no" by quietly running unlocked, which restores
#: the exact defect it was written to close and tells nobody. A guarantee
#: that evaporates on a platform the docs never excluded is worse than one
#: that was never claimed.
HAVE_LOCKING = fcntl is not None or msvcrt is not None


class UnlockablePlatformError(RuntimeError):
    """No cross-process file lock is available, so the ledger cannot keep
    its "an identical pair is one trial" promise. Construct the ledger with
    ``require_lock=False`` to proceed anyway — single-process use is
    unaffected, and saying so in code is the point."""


class TrialLedger:
    """Append-only record of evaluated strategy variants ("cells")."""

    def __init__(self, path: str | Path, *, require_lock: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: list[dict[str, Any]] | None = None
        self._size: int = -1
        if require_lock and not HAVE_LOCKING:      # pragma: no cover
            raise UnlockablePlatformError(
                "no cross-process file lock is available here (neither "
                "fcntl nor msvcrt), so two workers recording the same "
                "(name, params) can both append it and the trial count "
                "every deflation figure divides by stops describing the "
                "search. Pass require_lock=False to accept that.")
        self._require_lock = require_lock

    @staticmethod
    def _hash(params: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(params, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    # ── locking ─────────────────────────────────────────────────────────────
    @contextmanager
    def _lock(self, exclusive: bool):
        """Hold an advisory lock on the ledger for the duration of a block.

        Deduplication used to read the file, decide, and then append — three
        steps with two gaps in them. Two workers recording the same
        ``(name, params)`` both saw no row and both appended it, so an
        identical pair became two trials and every deflation figure divided
        by a number the search had not spent. Forcing the interleaving
        reproduced it every time.

        The lock is per open-file-description, so a caller holding the
        exclusive lock must NOT re-enter through a locking read — that is
        why ``_read_rows`` exists unlocked and ``record`` uses it directly.

        Where ``fcntl`` is unavailable this degrades to the previous
        behaviour rather than failing: single-process use is unaffected, and
        the check that matters is documented as advisory either way.
        """
        if not HAVE_LOCKING:                     # pragma: no cover
            yield None                           # opted in at construction
            return
        # Validate BEFORE touching or opening. The guard lived in
        # _read_rows, one step too late: acquiring the lock already did
        # touch() and open("r+") on whatever the path pointed at. On a
        # world-writable /dev/zero that happens to succeed and the refusal
        # arrives correctly; on a stricter box it raises PermissionError
        # from the lock setup instead. A guard whose answer depends on the
        # permissions of a device node is not a guard.
        if self.path.exists():
            check_record(self.path, "trial ledger")
        else:
            self.path.touch(exist_ok=True)
        handle = self.path.open("r+")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(),
                            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            else:                                # pragma: no cover - Windows
                # msvcrt has no shared mode, so a reader takes the exclusive
                # lock too: slower, never wrong. LK_LOCK retries ~10s and
                # then raises, which surfaces a stuck writer instead of
                # silently proceeding without the lock.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            yield handle
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                else:                            # pragma: no cover - Windows
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                handle.close()

    #: What a row must carry to be a trial at all: the identity dedup and
    #: the count are computed from, plus the shapes downstream reads.
    _ROW_SCHEMA = (("hash", str), ("name", str), ("params", dict))
    _ROW_OPTIONAL = (("note", str), ("metrics", dict), ("at", str))

    def _validate_row(self, row: dict[str, Any], line_no: int) -> dict[str, Any]:
        """LAYER 2 for a ledger line: is this parsed object a trial?

        ``{}`` parses, so it was COUNTED as a trial — and the count feeds
        every deflation figure — and then the next ``record()`` died on
        ``KeyError: 'hash'`` while deduplicating against it.

        A malformed row rejects the whole ledger. Not skipped, not counted,
        not repaired: a count that quietly excludes a row it could not read
        is exactly the undercount this file's dedup fix was written for,
        and guessing at a number the deflation divides by defeats the point
        of keeping one.
        """
        where = f"{self.path}:{line_no}"
        for field, kind in self._ROW_SCHEMA:
            if field not in row:
                raise RecordReadError(
                    f"trial ledger row at {where} has no {field!r} — a row "
                    "without it cannot be counted or deduplicated")
            if not isinstance(row[field], kind) or isinstance(row[field], bool):
                raise RecordReadError(
                    f"trial ledger row at {where} has {field!r} as a "
                    f"{type(row[field]).__name__}, expected {kind.__name__}")
        for field, kind in self._ROW_OPTIONAL:
            if field in row and not isinstance(row[field], kind):
                raise RecordReadError(
                    f"trial ledger row at {where} has {field!r} as a "
                    f"{type(row[field]).__name__}, expected {kind.__name__}")
        return row

    # ── reading ─────────────────────────────────────────────────────────────
    def _read_rows(self) -> list[dict[str, Any]]:
        """Parse the file. NO locking — callers already holding one use this
        directly; ``_scan`` is the locking wrapper."""
        rows: list[dict[str, Any]] = []
        if not self.path.exists():
            return rows
        # a ledger symlinked at an endless stream would loop here forever,
        # building lines, which is the same failure as reading one whole
        check_record(self.path, "trial ledger")
        with self.path.open() as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if line:
                    # layer 1: is it JSON, and an object at all — a row that
                    # is valid JSON but not one would surface as an
                    # AttributeError on .get() somewhere far from here.
                    # layer 2: is that object a trial.
                    rows.append(self._validate_row(
                        loads_mapping(line, "trial ledger row"), line_no))
        return rows

    def _scan(self, force: bool = False) -> list[dict[str, Any]]:
        """Rows, cached. Re-reads only when the file has changed size, so
        recording N trials costs O(N) reads and not O(N^2) — a 20k-cell
        sweep spent ~10 minutes re-parsing its own ledger before this.

        ``force`` skips the size heuristic. Size is a proxy for "changed"
        and two different rows can serialise to the same length, so the
        one place correctness depends on a fresh read — deduplication under
        the write lock — does not get to trust a proxy.
        """
        size = self.path.stat().st_size if self.path.exists() else -1
        if force or self._rows is None or size != self._size:
            with self._lock(exclusive=False):
                rows = self._read_rows()
            self._rows, self._size = rows, size
        return self._rows

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(list(self._scan()))

    def count(self) -> int:
        """Total distinct trials — the ``n_trials`` for ``stats.dsr`` and
        the ``n_cells`` for ``stats.expected_max_abs_t``."""
        return len(self._scan())

    # ── writing ─────────────────────────────────────────────────────────────
    def record(self, name: str, params: dict[str, Any], note: str = "",
               metrics: dict[str, Any] | None = None) -> str:
        """Append one evaluated cell. Returns its params-hash.

        Re-recording an identical (name, params) pair is a no-op — running
        the same cell twice is one trial, not two. ``metrics`` is free-form;
        a numeric ``"sr"`` key (PER-PERIOD Sharpe) is what
        ``sr_variance()`` reads.
        """
        # NESTED, not merged. `{"name": name, **params}` let a
        # params["name"] overwrite the strategy name, so two different
        # strategies sharing a parameter called "name" deduplicated into one
        # trial — silently UNDERCOUNTING the search that every deflation
        # figure depends on.
        h = self._hash({"strategy": name, "params": params})
        # Read, decide and append under ONE exclusive lock. Split apart,
        # two workers recording the same pair both saw no row and both
        # wrote it: an identical pair became two trials, and the count
        # every deflation figure divides by no longer described the search.
        with self._lock(exclusive=True) as handle:
            rows = self._read_rows()          # fresh, under the lock
            # Dedupe on the semantic pair as well as the stored hash, so a
            # ledger written by an older version still matches instead of
            # appending a duplicate and inflating the count.
            if any(r["hash"] == h
                   or (r.get("name") == name and r.get("params") == params)
                   for r in rows):
                self._rows, self._size = rows, self.path.stat().st_size
                return h
            row = {"hash": h, "name": name, "params": params, "note": note,
                   "metrics": dict(metrics) if metrics else {},
                   "at": datetime.now(timezone.utc).isoformat()}
            line = json.dumps(row, default=str) + "\n"
            if handle is not None:
                handle.seek(0, os.SEEK_END)   # the locked description
                handle.write(line)
                handle.flush()
            else:                             # pragma: no cover - no fcntl
                with self.path.open("a") as f:
                    f.write(line)
            rows.append(row)
            self._rows, self._size = rows, self.path.stat().st_size
        return h

    # ── what deflation needs ────────────────────────────────────────────────
    def sharpes(self) -> list[float]:
        """Recorded per-period Sharpes, in ledger order."""
        out = []
        for r in self._scan():
            v = (r.get("metrics") or {}).get("sr")
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and np.isfinite(v):
                out.append(float(v))
        return out

    def sr_variance(self) -> float | None:
        """Variance of per-period Sharpe ACROSS recorded trials — the
        ``sr_variance`` argument of ``stats.dsr``.

        Returns None when fewer than two trials carry an ``sr`` metric.
        None propagates through ``dsr`` as None (unmeasured), which is the
        point: a deflation computed against an invented spread is not a
        deflation, and both of this library's own demos used to invent one.
        """
        srs = self.sharpes()
        if len(srs) < 2:
            return None
        return float(np.var(srs, ddof=1))
