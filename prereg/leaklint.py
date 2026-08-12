"""Leak detection: static heuristics + a runtime prefix-replay check.

The static lint flags source patterns that CAN look ahead; each hit needs a
human eye — the point is that every flagged line gets one. The runtime check
is stronger: recompute a feature on a data PREFIX and compare against the
full-sample computation at the same rows. Any feature whose past values
change when the future is appended is leaking, whatever its source looks
like. (This check would have caught, on day one, a multi-timeframe
resampling leak that survived two years and every code review in the system
this library was extracted from.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
]


@dataclass
class LintHit:
    path: str
    line: int
    severity: str
    pattern: str
    message: str
    text: str


def lint_source(paths: list[str | Path]) -> list[LintHit]:
    """Scan python sources for lookahead-prone patterns."""
    hits: list[LintHit] = []
    for p in paths:
        p = Path(p)
        for i, line in enumerate(p.read_text().splitlines(), start=1):
            stripped = line.split("#", 1)[0]
            for pat, msg, sev in _PATTERNS:
                if re.search(pat, stripped):
                    hits.append(LintHit(str(p), i, sev, pat, msg,
                                        line.strip()))
    return hits


def prefix_replay_check(
    feature_fn: Callable[[pd.DataFrame], pd.DataFrame | pd.Series],
    data: pd.DataFrame,
    cut_fractions: tuple[float, ...] = (0.5, 0.75, 0.9),
    rtol: float = 1e-9,
    atol: float = 1e-12,
) -> dict:
    """THE leak test: past feature values must not change when the future
    is appended.

    feature_fn maps a time-indexed frame to a feature (same index). For each
    cut, compute on data[:cut] and on the full data, then compare the
    overlapping rows. Reports the first mismatching timestamp per cut.
    """
    full = pd.DataFrame(feature_fn(data))
    results, leaked = [], False
    for frac in cut_fractions:
        k = int(len(data) * frac)
        if k < 2:
            continue
        pref = pd.DataFrame(feature_fn(data.iloc[:k]))
        a = pref.to_numpy(dtype=float)
        b = full.iloc[:k].to_numpy(dtype=float)
        both = np.isfinite(a) & np.isfinite(b)
        mismatch = ~np.isclose(a, b, rtol=rtol, atol=atol) & both
        nan_mismatch = np.isfinite(a) != np.isfinite(b)
        bad = mismatch | nan_mismatch
        if bad.any():
            leaked = True
            first = int(np.nonzero(bad.any(axis=1))[0][0])
            results.append({"cut": frac, "leak": True,
                            "first_bad_row": str(data.index[first]),
                            "n_bad_rows": int(bad.any(axis=1).sum())})
        else:
            results.append({"cut": frac, "leak": False})
    return {"leak": leaked, "cuts": results}
