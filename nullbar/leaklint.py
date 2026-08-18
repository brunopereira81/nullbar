"""Leak detection: static heuristics + a runtime prefix-replay check.

The static lint flags source patterns that CAN look ahead; each hit needs a
human eye — the point is that every flagged line gets one. The runtime check
is stronger: recompute a feature on a data PREFIX and compare against the
full-sample computation at the same rows. Any feature whose past values
change when the future is appended is leaking, whatever its source looks
like. (This check would have caught, on day one, a multi-timeframe
resampling leak that survived two years and every code review in the system
this library was extracted from.)

A leak checker that cries wolf is a leak checker that gets switched off, so
the runtime check aligns on the index instead of on position (features that
drop warm-up rows are correct, not leaky) and compares non-numeric features
by equality instead of casting them to float.

CLI:  python -m nullbar.leaklint strategy/ features.py
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd

_PATTERNS: list[tuple[str, str, str]] = [
    (r"\.shift\(\s*-", "negative shift pulls FUTURE rows into the present",
     "high"),
    (r"center\s*=\s*True", "centered rolling windows use future data",
     "high"),
    (r"\.iloc\[[^\]]*\+\s*\d", "positive index offset may address the future",
     "medium"),
    (r"train_test_split\([^)]*shuffle\s*=\s*True",
     "shuffled split leaks future rows into training on time series",
     "high"),
    (r"\.quantile\(", "full-sample quantile used as a threshold is a "
     "lookahead unless expanding+shifted", "review"),
    (r"resample\(", "resampled higher-timeframe values must come from the "
     "previous COMPLETED bucket, not the one being formed", "review"),
    (r"fillna\(\s*method\s*=\s*['\"]bfill|\.bfill\(", "backfill copies "
     "future values into the past", "high"),
    (r"\.shift\(\s*periods\s*=\s*-", "negative shift (keyword form) pulls "
     "FUTURE rows into the present", "high"),
    (r"np\.roll\([^)]*,\s*-", "np.roll with a negative shift wraps future "
     "values into the present", "high"),
    (r"merge_asof\([^)]*direction\s*=\s*['\"]forward",
     "merge_asof(direction='forward') matches the NEXT record, which is the "
     "future at the time of the row it lands on", "high"),
]

_SUPPRESS = re.compile(r"#\s*(noqa:\s*leak|nullbar:\s*allow)", re.I)
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".tox", ".mypy_cache",
              ".pytest_cache", "build", "dist", "node_modules"}


class LeakError(AssertionError):
    """A feature changed its past when the future was appended."""


@dataclass
class LintHit:
    path: str
    line: int
    severity: str
    pattern: str
    message: str
    text: str


# ── static lint ─────────────────────────────────────────────────────────────
def _scannable_lines(src: str) -> list[str]:
    """Source with comments and multi-line strings blanked out.

    Not ``line.split("#")[0]``: that truncates at a ``#`` inside a string
    literal, so ``label = "close # then"; x = df.shift(-1)`` went unflagged
    — a false NEGATIVE in a leak detector. Tokenizing knows the difference.
    Docstrings are blanked because prose about ``shift(-1)`` is not code.
    """
    lines = src.splitlines()
    out = list(lines)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0] - 1] = out[tok.start[0] - 1][:tok.start[1]]
            elif tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                for ln in range(tok.start[0], tok.end[0] + 1):
                    out[ln - 1] = ""
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return [ln.split("#", 1)[0] for ln in lines]
    return out


def iter_python_files(paths: Iterable[str | Path]) -> list[Path]:
    """Expand a mix of files and directories into .py files."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*.py")
                              if not _SKIP_DIRS & set(f.parts)))
        else:
            out.append(p)
    return out


def lint_source(paths: Sequence[str | Path]) -> list[LintHit]:
    """Scan python sources for lookahead-prone patterns.

    Accepts files or directories. A line carrying ``# noqa: leak`` (or
    ``# nullbar: allow``) is skipped — a lint with no escape hatch gets
    deleted from CI the first time it is wrong.
    """
    hits: list[LintHit] = []
    for p in iter_python_files(paths):
        raw = p.read_text().splitlines()
        for i, code in enumerate(_scannable_lines(p.read_text()), start=1):
            if not code.strip() or _SUPPRESS.search(raw[i - 1]):
                continue
            for pat, msg, sev in _PATTERNS:
                if re.search(pat, code):
                    hits.append(LintHit(str(p), i, sev, pat, msg,
                                        raw[i - 1].strip()))
    return hits


# ── runtime prefix replay ───────────────────────────────────────────────────
def _as_frame(obj) -> pd.DataFrame:
    return obj.to_frame() if isinstance(obj, pd.Series) else pd.DataFrame(obj)


def _compare(a: pd.DataFrame, b: pd.DataFrame, rtol: float,
             atol: float) -> np.ndarray:
    """Boolean (rows x cols) matrix of disagreements, dtype-aware."""
    bad = np.zeros(a.shape, dtype=bool)
    for j, col in enumerate(a.columns):
        x, y = a[col], b[col]
        if pd.api.types.is_numeric_dtype(x) and \
                pd.api.types.is_numeric_dtype(y) and \
                not pd.api.types.is_bool_dtype(x):
            xv = x.to_numpy(dtype=float, na_value=np.nan)
            yv = y.to_numpy(dtype=float, na_value=np.nan)
            finite = np.isfinite(xv) & np.isfinite(yv)
            bad[:, j] = ((~np.isclose(xv, yv, rtol=rtol, atol=atol) & finite)
                         | (np.isfinite(xv) != np.isfinite(yv)))
        else:
            # strings, categories, booleans, timestamps: equality, with
            # null==null counting as agreement
            both_null = x.isna().to_numpy() & y.isna().to_numpy()
            eq = (x.to_numpy() == y.to_numpy()) | both_null
            bad[:, j] = ~eq
    return bad


def prefix_replay_check(
    feature_fn: Callable[[pd.DataFrame], pd.DataFrame | pd.Series],
    data: pd.DataFrame,
    cut_fractions: tuple[float, ...] = (0.5, 0.75, 0.9),
    rtol: float = 1e-9,
    atol: float = 1e-12,
) -> dict:
    """THE leak test: past feature values must not change when the future
    is appended.

    ``feature_fn`` maps a time-indexed frame to a feature. For each cut,
    compute on ``data[:cut]`` and on the full data, then compare the rows
    the two computations SHARE — aligned on the index, so a feature that
    drops its warm-up rows (``rolling(24).mean().dropna()``) is compared on
    what it does produce rather than reported as a leak for changing shape.
    Non-numeric features are compared by equality.

    Costs ``2 * len(cut_fractions) + 1`` calls to ``feature_fn``.

    Returns ``{"leak", "checked", "rows_compared", "cuts"}``. Read
    ``checked`` too: a run that compared nothing is not a clean bill of
    health — ``assert_no_leak`` enforces both.

    WHAT THIS CANNOT SEE. The check is sound in one direction only: a
    feature whose past changes IS leaking. The converse does not follow,
    and two common leaks are prefix-stable by construction, so they pass:

    1. A transform fitted on the whole sample outside the callable —
       ``MU, SD = df.mean(), df.std()`` then ``lambda d: (d - MU) / SD``.
       This is ``StandardScaler().fit(X)`` before the split, the most
       common leak in ML pipelines, and it is invisible here because the
       leak is baked into a constant.
    2. A callable that reads a global frame instead of its argument —
       ``lambda d: FULL["close"].shift(-1).reindex(d.index)``.

    Both have the same cure: pass a FIT-AND-TRANSFORM callable that derives
    everything it uses from the frame it is handed, and nothing from the
    enclosing scope. A leak the function cannot see is a leak this check
    cannot report, and no amount of cuts changes that.
    """
    full = _as_frame(feature_fn(data))
    results, leaked, compared_total = [], False, 0
    # Two cut points per fraction, 13 rows apart: a single cut can land
    # exactly on an aggregation boundary (midnight, week start), where a
    # bucket-level leak is invisible because the prefix's final bucket is
    # complete. Two cuts at a prime offset cannot both align with any
    # bucket size that matters. (Found the hard way: the day-boundary case
    # hid an MTF-style leak from this very check.)
    cut_points: list[tuple[float, int]] = []
    for frac in cut_fractions:
        k = int(len(data) * frac)
        cut_points.append((frac, k))
        cut_points.append((frac, k - 13))
    for frac, k in cut_points:
        if k < 2:
            continue
        pref = _as_frame(feature_fn(data.iloc[:k]))
        cols = [c for c in full.columns if c in set(pref.columns)]
        if not cols:
            results.append({"cut": frac, "leak": False, "compared": 0,
                            "note": "no shared columns"})
            continue
        if pref.index.is_unique and full.index.is_unique:
            shared = pref.index.intersection(full.index[:k])
            a, b = pref.loc[shared, cols], full.loc[shared, cols]
        else:                       # duplicate labels: positional overlap
            m = min(len(pref), k)
            a, b = pref.iloc[:m][cols], full.iloc[:m][cols]
            shared = a.index
        if len(shared) == 0:
            results.append({"cut": frac, "leak": False, "compared": 0,
                            "note": "no shared rows"})
            continue
        compared_total += len(shared)
        bad = _compare(a, b, rtol, atol)
        if bad.any():
            leaked = True
            first = int(np.nonzero(bad.any(axis=1))[0][0])
            results.append({"cut": frac, "leak": True, "compared": len(shared),
                            "first_bad_row": str(a.index[first]),
                            "n_bad_rows": int(bad.any(axis=1).sum())})
        else:
            results.append({"cut": frac, "leak": False,
                            "compared": len(shared)})
    return {"leak": leaked, "checked": compared_total > 0,
            "rows_compared": compared_total, "cuts": results}


def assert_no_leak(report: dict, name: str = "feature") -> None:
    """Raise unless the check actually ran AND found nothing.

    The two failure modes are different sentences: "it leaks" and "it never
    compared anything, so you have learned nothing".
    """
    if not report.get("checked"):
        raise LeakError(
            f"{name}: prefix-replay compared no rows — the feature produced "
            "nothing in common between prefix and full runs, so this is not "
            "a clean result")
    if report.get("leak"):
        first = next((c for c in report["cuts"] if c.get("leak")), {})
        raise LeakError(f"{name}: LEAK — past values changed when the future "
                        f"was appended (first at {first.get('first_bad_row')},"
                        f" {first.get('n_bad_rows')} rows at cut "
                        f"{first.get('cut')})")


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m nullbar.leaklint <paths...>`` — exits 1 on any hit."""
    ap = argparse.ArgumentParser(
        prog="nullbar-lint",
        description="Flag lookahead-prone patterns in python sources.")
    ap.add_argument("paths", nargs="+", help="files or directories")
    ap.add_argument("--severity", default="review",
                    choices=["high", "medium", "review"],
                    help="minimum severity to report (default: review = all)")
    ap.add_argument("--exit-zero", action="store_true",
                    help="always exit 0 (report only)")
    args = ap.parse_args(argv)
    order = {"high": 3, "medium": 2, "review": 1}
    floor = order[args.severity]
    hits = [h for h in lint_source(args.paths) if order[h.severity] >= floor]
    for h in hits:
        print(f"{h.path}:{h.line}: [{h.severity}] {h.message}\n    {h.text}")
    print(f"{len(hits)} hit(s) — each needs a human eye, "
          "or '# noqa: leak' saying why it is fine.")
    return 0 if (args.exit_zero or not hits) else 1


if __name__ == "__main__":
    sys.exit(main())
