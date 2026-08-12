"""Block-clustered evaluation and null controls.

``block_cluster_eval`` is the measurement convention that survived two years
of production falsification: one entry per asset per block, the BLOCK as the
unit of inference, and a per-asset-shuffled null control that must come back
flat before any real number is believed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .stats import clustered_t


def block_cluster_eval(mask: pd.DataFrame, fwd: pd.DataFrame,
                       block: str = "24h") -> dict:
    """Evaluate an entry mask against forward returns, honestly.

    mask, fwd: (time x asset) frames on identical axes; ``fwd`` in the
    units you want reported (e.g. percent over the holding period).
    One entry per asset per block; clustered t on block means.
    """
    if not mask.index.equals(fwd.index) or not mask.columns.equals(fwd.columns):
        raise ValueError("mask and fwd must share exact axes")
    sel = mask.fillna(False).to_numpy() & np.isfinite(fwd.to_numpy())
    ts, ss = np.nonzero(sel)
    if len(ts) == 0:
        return {"trades": 0, "clusters": 0, "gross": float("nan"),
                "cluster_mean": float("nan"), "t": float("nan")}
    df = pd.DataFrame({
        "time": mask.index.to_numpy()[ts],
        "asset": np.asarray(mask.columns)[ss],
        "fwd": fwd.to_numpy()[ts, ss],
    })
    df["block"] = pd.to_datetime(df["time"]).dt.floor(block)
    df = df.sort_values("time").groupby(["asset", "block"],
                                        as_index=False).first()
    t, cmean, n = clustered_t(df["fwd"], df["block"])
    per_year = {int(y): float(g.mean()) for y, g in
                df.groupby(pd.to_datetime(df["block"]).dt.year)["fwd"]}
    return {"trades": int(len(df)), "clusters": n,
            "gross": float(df["fwd"].mean()), "cluster_mean": cmean,
            "t": t, "per_year": per_year}


def shuffle_within_columns(fwd: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Destroy the signal-return relationship while preserving each asset's
    marginal return distribution — the null control input."""
    rng = np.random.default_rng(seed)
    arr = fwd.to_numpy().copy()
    for j in range(arr.shape[1]):
        col = arr[:, j]
        ok = np.isfinite(col)
        v = col[ok]
        rng.shuffle(v)
        col[ok] = v
    return pd.DataFrame(arr, index=fwd.index, columns=fwd.columns)


def null_control(mask: pd.DataFrame, fwd: pd.DataFrame, block: str = "24h",
                 seeds: tuple[int, ...] = (0, 1, 2)) -> list[dict]:
    """Run the pipeline on shuffled returns at several seeds.

    Run this BEFORE looking at the real result. Read it correctly: the
    shuffle preserves each asset's MARGINAL return distribution, so the
    null's gross converges to the unconditional (hold) mean — not to zero.
    On mean-zero-ish returns, null |t| ≈ 0; on drifting assets, compare the
    null against the hold baseline. If the null shows signal-like t beyond
    that baseline, the pipeline IS the effect and no real number from it
    can be believed.
    """
    return [block_cluster_eval(mask, shuffle_within_columns(fwd, s), block)
            for s in seeds]
