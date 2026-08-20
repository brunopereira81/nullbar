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


class BarMismatchError(ValueError):
    """The bar as written and the bar as evaluated disagree. The point of
    freezing a promise is that the code grading it says the same thing."""


_OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def spec_text(spec: dict[str, Any]) -> str:
    """Human rendering of a machine-checkable condition."""
    metric = f"|{spec['metric']}|" if spec.get("abs") else spec["metric"]
    return f"{metric} {spec['op']} {spec['value']}"


def _check_spec(spec: dict[str, Any], results: dict[str, Any]) -> bool | None:
    """Evaluate one registered condition against a results mapping.
    None means the metric is absent — reported as missing, never as False."""
    if spec["metric"] not in results:
        return None
    value = results[spec["metric"]]
    try:
        value = abs(float(value)) if spec.get("abs") else float(value)
    except (TypeError, ValueError):
        return None
    if value != value:                       # NaN is not a passing measurement
        return False
    return bool(_OPS[spec["op"]](value, float(spec["value"])))


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
        """``bar`` maps condition-name -> requirement.

        A requirement is either a human-readable string, graded by a boolean
        the caller computes, or a machine-checkable spec —
        ``{"metric": "t", "op": ">=", "value": 3.0}``, optionally with
        ``"abs": True`` — which ``verdict(results=...)`` evaluates itself.

        Prefer the spec. With prose, the frozen promise and the code that
        grades it are free to say different things forever: this library's
        own flagship demo froze "null-control |t| ~ 0" and graded it with
        ``worst < 3``, then passed on a null of 2.77. Where both are given,
        a disagreement raises ``BarMismatchError`` instead of picking one.
        """
        # NOT ``name`` — this loop shadowed the registration's own name
        # parameter, so every registration with a non-empty bar was stored
        # under the name of its LAST condition. Found by the first report
        # rendered off a frozen file, which is what an artifact is for.
        if not bar:
            raise ValueError(
                "bar must contain at least one condition — a registration "
                "with nothing to satisfy grades every result as PASS, which "
                "is the opposite of what freezing a bar is for")
        for cond, req in bar.items():
            if isinstance(req, dict):
                missing = {"metric", "op", "value"} - set(req)
                if missing:
                    raise ValueError(f"bar[{cond!r}] spec is missing "
                                     f"{sorted(missing)}")
                if req["op"] not in _OPS:
                    raise ValueError(
                        f"bar[{cond!r}] op {req['op']!r} is not one of "
                        f"{sorted(_OPS)}")
            elif not isinstance(req, str):
                raise TypeError(f"bar[{cond!r}] must be a string or a spec "
                                f"dict, got {type(req).__name__}")
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

    @staticmethod
    def _promise(doc: dict[str, Any]) -> str:
        """The part of a registration that is the promise: everything but
        the timestamp. Re-running the same script after a crash, or twice in
        CI, must not read as editing history — only the design and the bar
        moving does."""
        return json.dumps({k: v for k, v in doc.items() if k != "created_at"},
                          indent=2, sort_keys=True, default=str)

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
                old_doc = json.loads(old)
                if self._promise(old_doc) != self._promise(self.doc):
                    raise FileExistsError(
                        f"{p} already holds a different frozen registration "
                        "— write a new file; do not edit history")
                # identical promise, later timestamp: the file wins and its
                # hash is the one that counts.
                self.doc = old_doc
                digest = hashlib.sha256(old.encode()).hexdigest()
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

    def _read(self, p: Path) -> tuple[str, str]:
        """(text, sha256) of the frozen file — one read per verdict."""
        text = p.read_text()
        return text, hashlib.sha256(text.encode()).hexdigest()

    def _frozen_doc(self, p: Path,
                    known: tuple[str, str] | None = None) -> dict[str, Any]:
        """Read the registration from disk and refuse to proceed if it
        disagrees with the object in memory."""
        if known is None and not p.exists():
            raise SealBrokenError(
                f"{p} does not exist — a registration that has been deleted "
                "grades nothing; re-freeze under a new name and say so")
        text, on_disk = known if known is not None else self._read(p)
        in_memory = hashlib.sha256(self._payload().encode()).hexdigest()
        if on_disk != in_memory and \
                self._promise(json.loads(text)) != self._promise(self.doc):
            raise SealBrokenError(
                f"{p} holds {on_disk[:16]}… but the registration in memory "
                f"hashes to {in_memory[:16]}… — the design or the bar moved "
                "after freezing. Grade the frozen file "
                "(Registration.load(path)) or write a NEW registration.")
        return json.loads(text)

    def _stamp_path(self, reg_path: str | Path) -> Path:
        return Path(reg_path).with_suffix(".test_look.json")

    def seal_status(self, reg_path: str | Path | None = None,
                    known: tuple[str, str] | None = None) -> dict:
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
        out["sha256"] = (known[1] if known is not None
                         else self._read(p)[1])
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
    def evaluate(self, results: dict[str, Any]) -> dict[str, bool]:
        """Grade the machine-checkable conditions from a results mapping.

        Prose conditions are absent from the answer — nothing but the caller
        can grade those.
        """
        out = {}
        for name, req in self.doc["bar"].items():
            if isinstance(req, dict):
                checked = _check_spec(req, results)
                if checked is not None:
                    out[name] = checked
        return out

    def verdict(self, conditions: dict[str, Any] | None = None, *,
                results: dict[str, Any] | None = None,
                reg_path: str | Path | None = None,
                n_trials: int | None = None) -> dict[str, Any]:
        """Grade the frozen bar. Fail-closed in every direction.

        Every registered condition must be present AND unambiguously true;
        extra unregistered conditions are ignored (adding cells to find a
        passing one is the failure mode this library exists to prevent).
        A condition that is not a clean boolean — ``None``, ``NaN``, a
        float, a string — is reported under ``invalid`` and FAILS; an
        array-valued condition raises ``AmbiguousConditionError``.

        ``results`` grades every machine-checkable condition directly from
        your metrics, so the frozen promise and the code grading it cannot
        drift apart. Pass both ``conditions`` and ``results`` and any
        disagreement raises ``BarMismatchError`` rather than being resolved
        silently.

        ``n_trials`` checks the registered ``cells_budget``: a search that
        spent more cells than it promised fails, because the deflation the
        bar was set against no longer applies.

        The bar is read from the frozen file whenever one is known (from
        ``freeze()``, ``load()``, or ``reg_path=``), so an in-memory edit
        cannot lower it. ``verified`` says which happened.
        """
        p = self._resolve_path(reg_path)
        known = None
        if p is not None:
            if not p.exists():
                self._frozen_doc(p)              # raises SealBrokenError
            known = self._read(p)
            doc = self._frozen_doc(p, known)
            verified = True
        else:
            doc, verified = self.doc, False
        bar = doc["bar"]

        given = dict(conditions or {})
        computed: dict[str, bool] = {}
        if results is not None:
            for name, req in bar.items():
                if isinstance(req, dict):
                    checked = _check_spec(req, results)
                    if checked is not None:
                        computed[name] = checked

        disagreed = []
        for name, auto in computed.items():
            if name in given:
                state = _condition_state(given[name])
                if state in ("true", "false") and (state == "true") != auto:
                    disagreed.append(
                        f"{name}: registered {spec_text(bar[name])!r} "
                        f"evaluates to {auto}, caller passed "
                        f"{given[name]!r}")
        if disagreed:
            raise BarMismatchError(
                "the bar as written and the bar as evaluated disagree — "
                + "; ".join(disagreed))

        effective: dict[str, Any] = {**given, **computed}
        states = {k: _condition_state(effective[k]) for k in bar
                  if k in effective}
        missing = [k for k in bar if k not in effective]
        failed = [k for k in bar if states.get(k) in ("false", "invalid")]
        invalid = {k: f"{effective[k]!r} ({type(effective[k]).__name__})"
                   for k in bar if states.get(k) == "invalid"}

        budget = None
        if n_trials is not None:
            registered = doc.get("cells_budget")
            budget = {"registered": registered, "spent": int(n_trials),
                      "ok": registered is None
                      or int(n_trials) <= int(registered)}

        status = self.seal_status(p, known) if p is not None else None
        # ``bool(bar)`` is load-bearing. Freezing an empty bar is refused,
        # but a registration frozen by an older version — or hand-written —
        # still LOADS, and "no condition failed" is vacuously true of a
        # promise that registered nothing. That graded a record whose only
        # metric was t = -99 as a clean PASS. A registration that promises
        # nothing cannot pass; the report reads it as INCOMPLETE, which is
        # the honest answer: the record does not say.
        return {"pass": (bool(bar) and not missing and not failed
                         and (budget is None or budget["ok"])),
                "failed": failed, "missing": missing, "invalid": invalid,
                "budget": budget, "graded": sorted(computed),
                "bar": bar, "verified": verified,
                "sha256": status["sha256"] if status else None,
                "test_look_spent": (status["test_look_spent"]
                                    if status else None)}
