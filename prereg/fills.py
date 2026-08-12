"""Fill realism: a resting maker order is not a filled order.

Backtests that fill every limit order at its price overstate results in the
worst possible way — the entries that never fill are disproportionately the
best ones (price ran away). Measured in the production system this library
was extracted from: 92–96% of bids DID fill, but the missed 4–8% averaged
~+1.6%, so executed-trade gross was 0.66–0.79x the assumed gross.

The true fill rate is bracketed:
    TOUCH   — next-period low reached the limit (optimistic: assumes front
              of queue)
    THROUGH — price traded clearly past the limit (pessimistic: fills
              regardless of queue position)
Report BOTH; the truth is in between and only live resting orders can
narrow it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def touch_mask(limit: pd.DataFrame, low: pd.DataFrame,
               horizon: int = 1) -> pd.DataFrame:
    """Did the low within `horizon` future periods reach a bid at `limit`?

    ``limit`` is the resting price per (time x asset) — commonly the close
    of the signal bar. Strictly future periods only.
    """
    future_min = low.shift(-1).rolling(horizon, min_periods=1).min().shift(
        -(horizon - 1))
    return future_min <= limit


def through_mask(limit: pd.DataFrame, low: pd.DataFrame, horizon: int = 1,
                 margin: float = 5e-4) -> pd.DataFrame:
    """Queue-independent bound: price traded `margin` (fraction) past the
    bid."""
    return touch_mask(limit * (1.0 - margin), low, horizon)


def fill_bracket(mask: pd.DataFrame, limit: pd.DataFrame, low: pd.DataFrame,
                 fwd: pd.DataFrame, horizon: int = 1,
                 margin: float = 5e-4) -> dict:
    """Gross forward return of an entry mask under three fill assumptions.

    Returns per-assumption dicts of (n, gross) — the ratio
    ``touch.gross / assumed.gross`` is the honest haircut on every number
    the assumed-fills backtest produced.
    """
    base = mask.fillna(False) & fwd.notna()
    t_m = base & touch_mask(limit, low, horizon).fillna(False)
    th_m = base & through_mask(limit, low, horizon, margin).fillna(False)
    out = {}
    for name, m in (("assumed", base), ("touch", t_m), ("through", th_m)):
        v = fwd.to_numpy()[m.to_numpy()]
        out[name] = {"n": int(m.to_numpy().sum()),
                     "gross": float(np.nanmean(v)) if len(v) else float("nan")}
    return out
