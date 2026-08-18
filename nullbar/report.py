"""The report: the whole record on one page, with its gaps named.

A registration, its trial ledger and its test-look stamp are three files
that only mean something together. This module assembles them into one
artifact — the thing you hand a reader who has to decide whether to believe
a number, and who was not in the room while it was produced.

Two rules govern everything here:

1. **It reports the record; it does not re-run the research.** Every figure
   comes off disk. Nothing is recomputed from market data, because a report
   that recomputes can quietly report something the registration never
   graded. The only arithmetic done at report time is deflation — simulated
   from the recorded cell count and cluster count, seeded, and labelled as
   computed.
2. **A missing piece is stated, never omitted.** If the ledger was not
   supplied, the trial count is "not on the record" and every deflation
   figure downstream says so. An absent number that renders as a blank is
   how a report flatters; ``gaps`` collects them and the verdict degrades to
   INCOMPLETE rather than to PASS.

The payload the report reads is whatever was passed to
``Registration.spend_test_look(results=...)``. Build it with ``evidence()``
so the sections land where the report looks for them.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .ledger import TrialLedger
from .registration import (BarMismatchError, Registration, _condition_state,
                           spec_text)
from .stats import dsr as _dsr, expected_max_abs_t

#: Keys in a test-look payload that are SECTIONS (nested records), not
#: metrics the bar can grade. Everything else at top level is a metric.
SECTION_KEYS = ("null", "fills", "hold", "conditions")


def evidence(result: dict[str, Any] | None = None, *,
             null: dict[str, Any] | None = None,
             fills: dict[str, Any] | None = None,
             hold: dict[str, Any] | None = None,
             conditions: dict[str, Any] | None = None,
             **metrics: Any) -> dict[str, Any]:
    """Assemble the test-look payload: the sections, plus gradable metrics.

    ``spend_test_look`` records whatever it is handed, and a bar graded on
    metrics the stamp does not carry cannot be re-derived by anyone reading
    the record afterwards — this library's own demo spent its look on a
    ``block_cluster_eval`` dict while grading a bar that also needed the
    null control and the net figure. Pass the pieces here and the record is
    complete by construction.

    Derives the metric vocabulary the report and the bar can rely on:
    ``t``, ``gross``, ``cluster_mean``, ``trades``, ``clusters`` (from
    ``result``); ``null_max_abs_t``, ``null_ok``, ``null_expected_gross``;
    ``hold_gross``; ``assumed_gross``, ``touch_gross``, ``through_gross``,
    ``fill_haircut``. Anything passed as a keyword wins over a derived value
    of the same name — a derivation is a convenience, never an override.

    ``conditions`` carries booleans for prose bar entries, which nothing but
    the caller can grade. They are normalised through the same fail-closed
    classifier ``verdict()`` uses, so a ``None`` stays visible as invalid
    instead of being coerced to a quiet False.
    """
    out: dict[str, Any] = {}
    if result:
        out.update(result)
    if null is not None:
        out["null"] = null
        out["null_max_abs_t"] = null.get("max_abs_t_vs_expected")
        out["null_ok"] = bool(null.get("ok"))
        out["null_expected_gross"] = null.get("expected_gross")
        if hold is None and isinstance(null.get("hold"), dict):
            hold = null["hold"]          # null_verdict carries it already
    if hold is not None:
        out["hold"] = hold
        out["hold_gross"] = hold.get("gross")
    if fills is not None:
        out["fills"] = fills
        for key in ("assumed", "touch", "through"):
            leg = fills.get(key)
            if isinstance(leg, dict) and "gross" in leg:
                out[f"{key}_gross"] = leg["gross"]
        assumed, touch = out.get("assumed_gross"), out.get("touch_gross")
        if _finite(assumed) and _finite(touch) and float(assumed) != 0.0:
            out["fill_haircut"] = float(touch) / float(assumed)
    if conditions:
        graded = {}
        for name, value in conditions.items():
            state = _condition_state(value)       # raises on array-valued
            graded[name] = (True if state == "true" else
                            False if state == "false" else repr(value))
        out["conditions"] = graded
    out.update(metrics)
    return out


def _finite(value: Any) -> bool:
    """True only for a real, finite number — bools are not measurements."""
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _num(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def report_data(reg_path: str | Path, ledger_path: str | Path | None = None,
                *, sims: int = 100_000, seed: int = 0) -> dict[str, Any]:
    """Every fact the report renders, as a plain dict (JSON-serialisable).

    Separated from the rendering so the artifact can be asserted on directly
    — a test that has to parse HTML to check a verdict ends up asserting
    the template rather than the finding.
    """
    path = Path(reg_path)
    if not path.exists():
        raise FileNotFoundError(f"no registration at {path}")
    reg = Registration.load(path)
    frozen_text = path.read_text()
    doc = reg.doc
    gaps: list[str] = []
    findings: list[str] = []

    # ── the seal ────────────────────────────────────────────────────────────
    seal = reg.seal_status(path)
    stamp_path = path.with_suffix(".test_look.json")
    stamp: dict[str, Any] | None = None
    if stamp_path.exists():
        try:
            loaded = json.loads(stamp_path.read_text())
            stamp = loaded if isinstance(loaded, dict) else None
            if stamp is None:
                findings.append(
                    f"the test-look stamp at {stamp_path.name} is not a JSON "
                    "object — it records nothing a reader can check")
        except ValueError as exc:
            findings.append(f"the test-look stamp at {stamp_path.name} is "
                            f"unreadable ({exc}) — the look it records "
                            "cannot be bound to this registration")
    if stamp is not None and not seal["stamp_bound"]:
        findings.append(
            "the test look is NOT bound to this registration: the stamp "
            "names a different sha256, so the design or the bar moved after "
            "the held-out evaluation was spent")
    results: dict[str, Any] = {}
    if stamp is not None and isinstance(stamp.get("results"), dict):
        results = stamp["results"]
    elif stamp is not None:
        gaps.append("the test-look stamp carries no results mapping — the "
                    "bar cannot be re-graded from the record")
    if stamp is None:
        gaps.append("no test look has been spent: this registration records "
                    "a promise, not a held-out result")
    elif _before(stamp.get("at"), doc.get("created_at")):
        findings.append(
            f"the test look is stamped {stamp.get('at')}, BEFORE the "
            f"registration was created ({doc.get('created_at')}) — the "
            "promise cannot have preceded the result")

    # ── the search ──────────────────────────────────────────────────────────
    budget = doc.get("cells_budget")
    trials: dict[str, Any] = {"path": None, "count": None, "sr_variance": None,
                              "budget": budget, "over_budget": None}
    if ledger_path is not None:
        led_path = Path(ledger_path)
        trials["path"] = str(led_path)
        if led_path.exists():
            ledger = TrialLedger(led_path)
            trials["count"] = ledger.count()
            trials["sr_variance"] = ledger.sr_variance()
            if trials["sr_variance"] is None:
                gaps.append("fewer than two ledger trials recorded a Sharpe, "
                            "so the spread deflation needs is unmeasured")
        else:
            gaps.append(f"no trial ledger at {led_path} — the trial count is "
                        "not on the record")
    else:
        gaps.append("no trial ledger supplied (--ledger): the number of "
                    "variants actually searched is not on the record")
    if trials["count"] is not None and budget is not None:
        trials["over_budget"] = int(trials["count"]) > int(budget)
        if trials["over_budget"]:
            findings.append(
                f"the search spent {trials['count']} cells against a "
                f"registered budget of {budget} — the deflation the bar was "
                "set against no longer applies")

    # ── the verdict, graded off the frozen file ─────────────────────────────
    conditions = results.get("conditions")
    conditions = conditions if isinstance(conditions, dict) else None
    mismatch: str | None = None
    try:
        verdict = reg.verdict(conditions, results=results, reg_path=path,
                              n_trials=trials["count"])
    except BarMismatchError as exc:
        mismatch = str(exc)
        findings.append(f"the bar as written and the bar as recorded "
                        f"disagree: {exc}")
        verdict = reg.verdict(results=results, reg_path=path,
                              n_trials=trials["count"])
    graded_set = set(verdict["graded"])
    supplied = set(conditions or {})
    bar_rows = []
    for name, req in doc["bar"].items():
        machine = isinstance(req, dict)
        if name in verdict["invalid"]:
            state = "invalid"
        elif name in verdict["missing"]:
            state = "missing"
        elif name in verdict["failed"]:
            state = "fail"
        else:
            state = "pass"
        source = ("computed from the recorded metrics" if name in graded_set
                  else "supplied by the researcher" if name in supplied
                  else "not on the record")
        # the observed value, not just the verdict: a PASS whose number the
        # reader cannot see is a claim, not evidence.
        observed = results.get(req["metric"]) if machine else None
        bar_rows.append({"name": name, "machine_checkable": machine,
                         "requirement": spec_text(req) if machine else req,
                         "metric": req["metric"] if machine else None,
                         "observed": observed if _finite(observed) else None,
                         "state": state, "source": source,
                         "detail": verdict["invalid"].get(name)})
    if verdict["missing"]:
        gaps.append("the record does not carry a grade for: "
                    + ", ".join(verdict["missing"]))

    status = _status(verdict, stamp, mismatch)

    data: dict[str, Any] = {
        "nullbar_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registration": {
            "name": doc.get("name"), "hypothesis": doc.get("hypothesis"),
            "design": doc.get("design", {}), "created_at": doc.get("created_at"),
            "cells_budget": budget, "path": str(path),
            "sha256": seal["sha256"], "frozen_text": frozen_text,
        },
        "seal": {**seal, "stamp_path": str(stamp_path),
                 "test_look_at": (stamp or {}).get("at"),
                 "stamp_sha256": (stamp or {}).get("registration_sha256")},
        "trials": trials,
        "result": {k: results[k] for k in
                   ("trades", "clusters", "gross", "cluster_mean", "t",
                    "per_year") if k in results} or None,
        "null": results.get("null") if isinstance(results.get("null"), dict)
                else None,
        "hold": results.get("hold") if isinstance(results.get("hold"), dict)
                else None,
        "fills": results.get("fills") if isinstance(results.get("fills"), dict)
                 else None,
        "metrics": {k: v for k, v in results.items()
                    if k not in SECTION_KEYS},
        "verdict": {**verdict, "rows": bar_rows, "status": status,
                    "mismatch": mismatch},
        "findings": findings,
    }
    data["deflation"] = _deflation(results, trials, budget, gaps,
                                   sims=sims, seed=seed)
    data["gaps"] = gaps
    return data


def _before(a: Any, b: Any) -> bool:
    """Is timestamp ``a`` strictly before ``b``? False if either is unusable
    — an unparseable timestamp is not evidence of an ordering violation."""
    try:
        return datetime.fromisoformat(str(a)) < datetime.fromisoformat(str(b))
    except (TypeError, ValueError):
        return False


def _status(verdict: dict[str, Any], stamp: dict[str, Any] | None,
            mismatch: str | None) -> str:
    """PASS / FAIL / INCOMPLETE / CONTRADICTED — never a bare boolean.

    INCOMPLETE exists because "the record does not say" and "the strategy
    failed" are different answers, and collapsing them is how an unfinished
    record reads as a result.
    """
    if mismatch is not None:
        return "CONTRADICTED"
    if stamp is None:
        return "INCOMPLETE"
    if verdict["pass"]:
        return "PASS"
    if verdict["failed"] or verdict["invalid"] or (
            verdict["budget"] is not None and not verdict["budget"]["ok"]):
        return "FAIL"
    return "INCOMPLETE"


def _deflation(results: dict[str, Any], trials: dict[str, Any],
               budget: int | None, gaps: list[str], *, sims: int,
               seed: int) -> dict[str, Any]:
    """The luck thresholds, computed at report time from recorded inputs.

    The 95th percentile is the bar: pure noise beats its own EXPECTED
    maximum about 45% of the time, so the mean of max|t| is a description of
    where noise sits, not a threshold anything has to clear.
    """
    out: dict[str, Any] = {
        "n_cells": None, "n_cells_source": None, "df": None,
        "threshold_95": None, "median": None, "observed_abs_t": None,
        "clears": None, "dsr": None, "dsr_source": None, "sr": None,
        "sr_source": None, "sims": sims, "seed": seed,
    }
    if trials["count"] is not None:
        out["n_cells"], out["n_cells_source"] = int(trials["count"]), "ledger"
    elif budget is not None:
        out["n_cells"] = int(budget)
        out["n_cells_source"] = "registered cells_budget (a promise, not a count)"

    clusters = results.get("clusters")
    n_clusters = int(clusters) if _finite(clusters) else None
    if n_clusters is not None and n_clusters >= 2:
        out["df"] = n_clusters - 1
    elif out["n_cells"] is not None:
        gaps.append("the cluster count is not on the record, so the luck "
                    "threshold uses the normal approximation, which "
                    "UNDERSTATES it")

    if out["n_cells"] is not None and out["n_cells"] >= 1:
        kw = {"df": out["df"], "n_sims": sims, "seed": seed}
        out["threshold_95"] = expected_max_abs_t(out["n_cells"],
                                                 summary=0.95, **kw)
        out["median"] = expected_max_abs_t(out["n_cells"],
                                           summary="median", **kw)
        out["observed_abs_t"] = (abs(_num(results.get("t")))
                                 if _finite(results.get("t")) else None)
        if out["observed_abs_t"] is not None:
            out["clears"] = out["observed_abs_t"] >= out["threshold_95"]

    if _finite(results.get("sr")):
        out["sr"], out["sr_source"] = _num(results["sr"]), "recorded"
    elif _finite(results.get("t")) and n_clusters and n_clusters >= 1:
        out["sr"] = float(results["t"]) / math.sqrt(n_clusters)
        out["sr_source"] = "derived: t / sqrt(clusters), per cluster"

    if _finite(results.get("dsr")):
        out["dsr"], out["dsr_source"] = _num(results["dsr"]), "recorded"
    elif (out["sr"] is not None and n_clusters is not None
            and trials["count"] is not None
            and trials["sr_variance"] is not None):
        out["dsr"] = _dsr(out["sr"], n=n_clusters, n_trials=trials["count"],
                          sr_variance=trials["sr_variance"])
        out["dsr_source"] = ("computed at report time from the ledger's "
                             "count and Sharpe spread")
    else:
        gaps.append("the deflated Sharpe is unmeasured — it needs the trial "
                    "count AND the spread of Sharpes across the search")
    return out
