"""Trial ledger — the number nobody wants to remember.

Every deflated statistic needs the TOTAL count of variants ever evaluated in
the search, including the embarrassing ones. Human memory reliably reports
"just this one idea"; the ledger is append-only JSONL that reports the truth.

There is deliberately NO delete API. If a trial was run, it counts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class TrialLedger:
    """Append-only record of evaluated strategy variants ("cells")."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash(params: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(params, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    def record(self, name: str, params: dict[str, Any],
               note: str = "") -> str:
        """Append one evaluated cell. Returns its params-hash.

        Re-recording an identical (name, params) pair is a no-op — running
        the same cell twice is one trial, not two.
        """
        h = self._hash({"name": name, **params})
        for row in self:
            if row["hash"] == h:
                return h
        row = {"hash": h, "name": name, "params": params, "note": note,
               "at": datetime.now(timezone.utc).isoformat()}
        with self.path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return h

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def count(self) -> int:
        """Total distinct trials — the ``n_trials`` for ``stats.dsr`` and
        the ``n_cells`` for ``stats.expected_max_abs_t``."""
        return sum(1 for _ in self)
