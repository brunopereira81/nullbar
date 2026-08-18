"""Pre-registration: commit the design and the bar before the result exists.

The workflow this enforces:

1. Write the design (hypothesis, fixed parameters, evaluation terms) and the
   PASS BAR (named boolean conditions) BEFORE running anything.
2. ``freeze()`` — the registration is hashed and becomes immutable; any later
   edit changes the hash and is visible.
3. Run freely on TRAINING/VALIDATION data.
4. The held-out test evaluation is ONE ``spend_test_look()`` — a second call
   raises. There is no "one more look".
5. ``verdict()`` grades results against the bar AS FROZEN ON DISK, not the
   copy in memory, and every condition must be *unambiguously* true.

What this does NOT do: it is tamper-EVIDENT, not tamper-PROOF. Anyone with
write access can delete the frozen file and the test-look stamp and start
over. Commit both to version control (or any append-only store) if a third
party has to believe them; the hash in the stamp is what binds the two.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AlreadySpentError(RuntimeError):
    """The single test look was already taken."""


class SealBrokenError(RuntimeError):
    """The frozen registration on disk no longer matches what is being
    graded — the design or the bar moved after freezing."""


class AmbiguousConditionError(ValueError):
    """A pass condition was array-valued. Reduce it yourself (``.all()`` /
    ``.any()``) — guessing which one you meant is how a bar gets lowered."""


def _condition_state(value: Any) -> str:
    """Classify a pass condition: 'true', 'false', or 'invalid'.

    Fail-closed by construction. ONLY an unambiguous boolean true reads as
    true; ``None``, ``NaN``, ``0``, ``""``, a float, a string and anything
    else read as 'invalid' (which fails). Array-valued conditions raise.

    Why this is not ``bool(value)``: ``bool(None)`` and ``bool(0.0)`` are
    False, which is the right verdict for the wrong reason — the caller
    computed something that was never a test — and ``bool(np.array([1, 2]))``
    raises deep inside a verdict instead of at the call site.
    """
    if value is True:
        return "true"
    if value is False:
        return "false"
    ndim = getattr(value, "ndim", None)
    if ndim is not None and ndim > 0:
        raise AmbiguousConditionError(
            f"array-valued condition (ndim={ndim}); reduce it explicitly "
            "with .all() or .any() — a verdict may not guess")
    dtype = getattr(value, "dtype", None)
    if dtype is not None and getattr(dtype, "kind", None) == "b":
        # numpy / pandas scalar boolean: np.False_ is NOT the False
        # singleton, which is exactly how a failing bar reads as PASS.
        return "true" if bool(value) else "false"
    return "invalid"


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
        self.path: Path | None = None
        self.sha256: str | None = None

    # ── freezing ────────────────────────────────────────────────────────────
    def _payload(self) -> str:
        return json.dumps(self.doc, indent=2, sort_keys=True, default=str)

    def freeze(self, path: str | Path) -> str:
        """Write the registration; returns its sha256. Refuses to overwrite
        an existing registration with different content — a frozen design
        does not get edited, it gets superseded by a NEW registration."""
        p = Path(path)
        payload = self._payload()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if p.exists():
            old = p.read_text()
            if hashlib.sha256(old.encode()).hexdigest() != digest:
                raise FileExistsError(
                    f"{p} already holds a different frozen registration — "
                    "write a new file; do not edit history")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(payload)
        self.path, self.sha256 = p, digest
        return digest

    @staticmethod
    def load(path: str | Path) -> "Registration":
        p = Path(path)
        text = p.read_text()
        r = Registration.__new__(Registration)
        r.doc = json.loads(text)
        r.path = p
        r.sha256 = hashlib.sha256(text.encode()).hexdigest()
        return r

    # ── the seal ────────────────────────────────────────────────────────────
    def _resolve_path(self, reg_path: str | Path | None) -> Path | None:
        if reg_path is not None:
            return Path(reg_path)
        return self.path

    def _frozen_doc(self, p: Path) -> dict[str, Any]:
        """Read the registration from disk and refuse to proceed if it
        disagrees with the object in memory."""
        if not p.exists():
            raise SealBrokenError(
                f"{p} does not exist — a registration that has been deleted "
                "grades nothing; re-freeze under a new name and say so")
        text = p.read_text()
        on_disk = hashlib.sha256(text.encode()).hexdigest()
        in_memory = hashlib.sha256(self._payload().encode()).hexdigest()
        if on_disk != in_memory:
            raise SealBrokenError(
                f"{p} holds {on_disk[:16]}… but the registration in memory "
                f"hashes to {in_memory[:16]}… — the design or the bar moved "
                "after freezing. Grade the frozen file "
                "(Registration.load(path)) or write a NEW registration.")
        return json.loads(text)

    def _stamp_path(self, reg_path: str | Path) -> Path:
        return Path(reg_path).with_suffix(".test_look.json")

    def seal_status(self, reg_path: str | Path | None = None) -> dict:
        """Everything a reader needs to judge whether the seal held.

        ``matches`` is False (never an exception) so this can be called on a
        broken seal — it is the one method whose job is to report one.
        """
        p = self._resolve_path(reg_path)
        out: dict[str, Any] = {"path": str(p) if p else None,
                               "frozen": bool(p and p.exists()),
                               "sha256": None, "matches": False,
                               "test_look_spent": False, "stamp_bound": False}
        if not out["frozen"]:
            return out
        text = p.read_text()
        out["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        out["matches"] = out["sha256"] == hashlib.sha256(
            self._payload().encode()).hexdigest()
        stamp = self._stamp_path(p)
        if stamp.exists():
            out["test_look_spent"] = True
            try:
                out["stamp_bound"] = (json.loads(stamp.read_text())
                                      .get("registration_sha256")
                                      == out["sha256"])
            except (ValueError, OSError):
                out["stamp_bound"] = False
        return out

    # ── the single test look ────────────────────────────────────────────────
    def spend_test_look(self, reg_path: str | Path,
                        results: dict[str, Any]) -> None:
        """Record the one held-out evaluation. A second call raises
        AlreadySpentError — loudly, with the timestamp of the first.

        The stamp carries the registration's sha256, so a look is bound to
        the design it was spent on: swapping the registration afterwards is
        visible instead of silently inheriting the spent look.
        """
        p = Path(reg_path)
        self._frozen_doc(p)                       # seal must hold first
        stamp = self._stamp_path(p)
        if stamp.exists():
            prior = json.loads(stamp.read_text())
            raise AlreadySpentError(
                f"test look already spent at {prior['at']} — a second look "
                "certifies nothing and violates the registration")
        stamp.write_text(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "registration": self.doc.get("name"),
            "registration_sha256": self.sha256 or hashlib.sha256(
                self._payload().encode()).hexdigest(),
            "results": results,
        }, indent=2, default=str))

    # ── the verdict ─────────────────────────────────────────────────────────
    def verdict(self, conditions: dict[str, Any],
                reg_path: str | Path | None = None) -> dict[str, Any]:
        """Grade the frozen bar. Fail-closed in both directions.

        Every registered condition must be present AND unambiguously true;
        extra unregistered conditions are ignored (adding cells to find a
        passing one is the failure mode this library exists to prevent).
        A condition that is not a clean boolean — ``None``, ``NaN``, a
        float, a string — is reported under ``invalid`` and FAILS; an
        array-valued condition raises ``AmbiguousConditionError``.

        The bar is read from the frozen file whenever one is known (from
        ``freeze()``, ``load()``, or ``reg_path=``), so an in-memory edit
        cannot lower it. ``verified`` says which happened.
        """
        p = self._resolve_path(reg_path)
        if p is not None:
            doc = self._frozen_doc(p)
            verified = True
        else:
            doc, verified = self.doc, False
        bar = doc["bar"]

        states = {}
        for k in bar:
            if k in conditions:
                states[k] = _condition_state(conditions[k])
        missing = [k for k in bar if k not in conditions]
        failed = [k for k in bar if states.get(k) in ("false", "invalid")]
        invalid = {k: f"{conditions[k]!r} ({type(conditions[k]).__name__})"
                   for k in bar if states.get(k) == "invalid"}
        status = self.seal_status(p) if p is not None else None
        return {"pass": not missing and not failed,
                "failed": failed, "missing": missing, "invalid": invalid,
                "bar": bar, "verified": verified,
                "sha256": status["sha256"] if status else None,
                "test_look_spent": (status["test_look_spent"]
                                    if status else None)}
