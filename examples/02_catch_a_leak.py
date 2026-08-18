"""Catching lookahead leaks with the prefix-replay check.

Run:  python examples/02_catch_a_leak.py

Three feature functions on the same data. One is honest. Two leak — one of
them in the exact shape that survived two years in the production system
this library was extracted from: higher-timeframe aggregates mapped onto the
lower-timeframe bars INSIDE the aggregation bucket, so every hour sees its
own day's close, which happens in the future.

The check is one idea: recompute the feature on a PREFIX of the data. If any
past value changes when the future is appended, the feature is leaking —
regardless of how innocent the code looks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nullbar import lint_source, prefix_replay_check

rng = np.random.default_rng(3)
idx = pd.date_range("2024-01-01", periods=24 * 60, freq="h", tz="UTC")
df = pd.DataFrame({"close": 100 + rng.normal(0, 1, len(idx)).cumsum()},
                  index=idx)


def causal_feature(d: pd.DataFrame) -> pd.Series:
    """Distance from a trailing moving average — honest."""
    return np.log(d["close"] / d["close"].rolling(168, min_periods=168).mean())


def zscore_leak(d: pd.DataFrame) -> pd.Series:
    """Full-sample normalization — every past value uses the future's
    mean and std. The most common leak in ML feature pipelines."""
    return (d["close"] - d["close"].mean()) / d["close"].std()


def mtf_leak(d: pd.DataFrame) -> pd.Series:
    """The production classic: map each hour to its own DAY's closing
    value. The 01:00 bar sees a number decided at 23:00 — up to 23 hours
    of future — and it passes every eyeball review, because resampling
    looks like bookkeeping, not prediction."""
    daily = d["close"].resample("1D").last()
    return daily.reindex(d.index, method="ffill")


for name, fn in [("causal_feature", causal_feature),
                 ("zscore_leak", zscore_leak),
                 ("mtf_leak", mtf_leak)]:
    r = prefix_replay_check(fn, df)
    flag = "LEAK" if r["leak"] else "clean"
    detail = ""
    if r["leak"]:
        first = next(c for c in r["cuts"] if c.get("leak"))
        detail = f"  (first bad row at {first['first_bad_row']})"
    print(f"{name:16s} -> {flag}{detail}")

print("\nStatic lint of this very file:")
for h in lint_source([__file__]):
    print(f"  line {h.line:3d} [{h.severity}] {h.message}")
