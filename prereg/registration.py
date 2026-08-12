"""Pre-registration: commit the design and the bar before the result exists.

The workflow this enforces:

1. Write the design (hypothesis, fixed parameters, evaluation terms) and the
   PASS BAR (named boolean conditions) BEFORE running anything.
2. ``freeze()`` — the registration is hashed and becomes immutable; any later
   edit changes the hash and is visible.
3. Run freely on TRAINING/VALIDATION data.
4. The held-out test evaluation is ONE ``spend_test_look()`` — a second call
   raises. There is no "one more look".
5. ``verdict()`` checks results against the bar as written. The bar cannot
   move after seeing numbers because it is part of the frozen hash.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AlreadySpentError(RuntimeError):
    """The single test look was already taken."""


class Registration:
    def __init__(self, name: str, hypothesis: str, design: dict[str, Any],
                 bar: dict[str, str], cells_budget: int = 1) -> None:
        """``bar`` maps condition-name -> human-readable requirement.
        Conditions are evaluated by the CALLER (they know their metrics);
        the registration records what was promised and refuses amnesia.
        """
        self.doc: dict[str, Any] = {
            "name": name,
            "hypothesis": hypothesis,
            "design": design,
            "bar": bar,
            "cells_budget": cells_budget,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def freeze(self, path: str | Path) -> str:
        """Write the registration; returns its sha256. Refuses to overwrite
        an existing registration with different content — a frozen design
        does not get edited, it gets superseded by a NEW registration."""
        p = Path(path)
        payload = json.dumps(self.doc, indent=2, sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if p.exists():
            old = p.read_text()
            if hashlib.sha256(old.encode()).hexdigest() != digest:
                raise FileExistsError(
                    f"{p} already holds a different frozen registration — "
                    "write a new file; do not edit history")
            return digest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload)
        return digest

    @staticmethod
    def load(path: str | Path) -> "Registration":
        doc = json.loads(Path(path).read_text())
        r = Registration.__new__(Registration)
        r.doc = doc
        return r

    # ── the single test look ────────────────────────────────────────────────
    def _stamp_path(self, reg_path: str | Path) -> Path:
        return Path(reg_path).with_suffix(".test_look.json")

    def spend_test_look(self, reg_path: str | Path,
                        results: dict[str, Any]) -> None:
        """Record the one held-out evaluation. A second call raises
        AlreadySpentError — loudly, with the timestamp of the first."""
        stamp = self._stamp_path(reg_path)
        if stamp.exists():
            prior = json.loads(stamp.read_text())
            raise AlreadySpentError(
                f"test look already spent at {prior['at']} — a second look "
                "certifies nothing and violates the registration")
        stamp.write_text(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "results": results,
        }, indent=2, default=str))

    def verdict(self, conditions: dict[str, bool]) -> dict[str, Any]:
        """Evaluate the frozen bar. Every registered condition must be
        present and True to pass; extra unregistered conditions are ignored
        (adding cells to find a passing one is the failure mode this
        library exists to prevent)."""
        bar = self.doc["bar"]
        missing = [k for k in bar if k not in conditions]
        failed = [k for k in bar if conditions.get(k) is False]
        passed = not missing and not failed
        return {"pass": passed, "failed": failed, "missing": missing,
                "bar": bar}
