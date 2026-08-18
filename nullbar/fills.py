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

Both sides are modelled: ``side="buy"`` is a resting bid measured against
the LOW frame, ``side="sell"`` a resting ask measured against the HIGH.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ._align import require_same_axes


def touch_mask(limit: pd.DataFrame, extreme: pd.DataFrame,
               horizon: int = 1, side: str = "buy") -> pd.DataFrame:
    """Did price come to a resting order within `horizon` future periods?

    ``limit`` is the resting price per (time x asset) — commonly the close
    of the signal bar. Strictly future periods only.

    ``side="buy"``  — a resting BID: pass the LOW frame as ``extreme``;
    it fills if the low reached the bid.
    ``side="sell"`` — a resting ASK: pass the HIGH frame; it fills if the
    high reached the ask.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    require_same_axes(limit=limit, extreme=extreme)
    # Looking at the FUTURE extreme is the point here: a resting order fills
    # when the market comes to it AFTER the signal bar. Nothing about the
    # DECISION uses it — which is exactly the judgement call the lint's
    # escape hatch is for, and why it demands a reason.
    nxt = extreme.shift(-1)                # noqa: leak — fill, not signal
    roll = nxt.rolling(horizon, min_periods=1)
    if side == "buy":
        return roll.min().shift(-(horizon - 1)) <= limit   # noqa: leak
    return roll.max().shift(-(horizon - 1)) >= limit       # noqa: leak


def through_mask(limit: pd.DataFrame, extreme: pd.DataFrame,
                 horizon: int = 1, margin: float = 5e-4,
                 side: str = "buy") -> pd.DataFrame:
    """Queue-independent bound: price traded `margin` (fraction) past the
    resting order — below a bid, above an ask."""
    adjusted = limit * (1.0 - margin) if side == "buy" else \
        limit * (1.0 + margin)
    return touch_mask(adjusted, extreme, horizon, side)


def fill_bracket(mask: pd.DataFrame, limit: pd.DataFrame,
                 extreme: pd.DataFrame, fwd: pd.DataFrame, horizon: int = 1,
                 margin: float = 5e-4, side: str = "buy") -> dict:
    """Gross forward return of an entry mask under three fill assumptions.

    ``extreme`` is the LOW frame for a resting bid (``side="buy"``, the
    default) and the HIGH frame for a resting ask (``side="sell"``).

    All four frames must share exact axes — this function indexes them
    against each other positionally, so a column reordering would report
    another asset's returns for this asset's fills. (Measured: a swap of two
    columns turned a true gross of 1.0 into 9.0, silently, in the module
    whose whole job is to correct a 1.3–1.5x overstatement.)

    Returns per-assumption dicts of (n, gross) — the ratio
    ``touch.gross / assumed.gross`` is the honest haircut on every number
    the assumed-fills backtest produced.
    """
    require_same_axes(mask=mask, limit=limit, extreme=extreme, fwd=fwd)
    base = mask.fillna(False).astype(bool) & fwd.notna()
    t_m = base & touch_mask(limit, extreme, horizon, side).fillna(False)
    th_m = base & through_mask(limit, extreme, horizon, margin,
                               side).fillna(False)
    out = {}
    for name, m in (("assumed", base), ("touch", t_m), ("through", th_m)):
        v = fwd.to_numpy()[m.to_numpy()]
        out[name] = {"n": int(m.to_numpy().sum()),
                     "gross": float(np.nanmean(v)) if len(v) else float("nan")}
    return out
