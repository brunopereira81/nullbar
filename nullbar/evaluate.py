"""Block-clustered evaluation and null controls.

``block_cluster_eval`` is the measurement convention that survived two years
of production falsification: one entry per asset per block, the BLOCK as the
unit of inference, and a per-asset-shuffled null control that must come back
flat before any real number is believed.

"Flat" means flat AGAINST THE HOLD BASELINE, not against zero — shuffling
preserves each asset's marginal distribution, so on drifting assets the null
inherits the drift. ``null_verdict`` performs that comparison; reading a
null's raw |t| as if zero were the reference is how a null control gets
quoted as OK next to an effect it actually exceeds.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ._align import require_same_axes
from .stats import clustered_t


def _entries(mask: pd.DataFrame, fwd: pd.DataFrame,
             block: str) -> pd.DataFrame:
    """One entry per asset per block: (asset, block, fwd)."""
    require_same_axes(mask=mask, fwd=fwd)
    sel = mask.fillna(False).astype(bool).to_numpy() & np.isfinite(
        fwd.to_numpy())
    ts, ss = np.nonzero(sel)
    if len(ts) == 0:
        return pd.DataFrame({"time": [], "asset": [], "fwd": [], "block": []})
    df = pd.DataFrame({
        "time": mask.index.to_numpy()[ts],
        "asset": np.asarray(mask.columns)[ss],
        "fwd": fwd.to_numpy()[ts, ss],
    })
    df["block"] = pd.to_datetime(df["time"]).dt.floor(block)
    return df.sort_values("time").groupby(["asset", "block"],
                                          as_index=False).first()


def block_cluster_eval(mask: pd.DataFrame, fwd: pd.DataFrame,
                       block: str = "24h") -> dict:
    """Evaluate an entry mask against forward returns, honestly.

    mask, fwd: (time x asset) frames on identical axes; ``fwd`` in the
    units you want reported (e.g. percent over the holding period).
    One entry per asset per block; clustered t on block means.
    """
    df = _entries(mask, fwd, block)
    if len(df) == 0:
        # every key the caller reads on the happy path must exist here too:
        # a strategy that took no trades is exactly when a KeyError lands.
        return {"trades": 0, "clusters": 0, "gross": float("nan"),
                "cluster_mean": float("nan"), "t": float("nan"),
                "per_year": {}}
    t, cmean, n = clustered_t(df["fwd"], df["block"])
    per_year = {int(y): float(g.mean()) for y, g in
                df.groupby(pd.to_datetime(df["block"]).dt.year)["fwd"]}
    return {"trades": int(len(df)), "clusters": n,
            "gross": float(df["fwd"].mean()), "cluster_mean": cmean,
            "t": t, "per_year": per_year}


def hold_baseline(fwd: pd.DataFrame, block: str = "24h") -> dict:
    """What doing nothing pays: every asset, every block, unconditionally.

    The reference the null control converges to, and the one a strategy has
    to beat to have earned its trading. Same units and same clustering as
    ``block_cluster_eval`` so the two are directly comparable.
    """
    ones = pd.DataFrame(True, index=fwd.index, columns=fwd.columns)
    return block_cluster_eval(ones, fwd, block)


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
    Use ``null_verdict`` to make that comparison rather than reading these
    |t| values against zero.
    """
    return [block_cluster_eval(mask, shuffle_within_columns(fwd, s), block)
            for s in seeds]


def null_verdict(mask: pd.DataFrame, fwd: pd.DataFrame, block: str = "24h",
                 seeds: tuple[int, ...] = (0, 1, 2),
                 max_abs_t: float = 3.0) -> dict:
    """Does the evaluation machinery invent an effect on scrambled returns?

    Shuffling within a column preserves that asset's marginal distribution,
    so a shuffled run should reproduce exactly one thing: the unconditional
    mean of the assets the mask holds, weighted the way the mask holds them.
    That composition-matched expectation is the reference — NOT zero, and
    not an equal-weight buy-and-hold either.

    Why not equal-weight hold: the blocks a strategy trades in are selected
    (that is what a strategy is), so an equal-weight baseline restricted to
    those blocks carries the very effect under test, and any asset tilt adds
    a second bias. Measured on real data, comparing against it reported
    |t| = 5.06 — "pipeline broken" — for a pipeline that was fine.

    ``ok`` is therefore a machinery check: alignment, block assignment, NaN
    handling, and the shuffle itself add nothing beyond composition. It is
    NOT a statement that the strategy has an edge — for that, compare the
    real result against ``expected_gross`` (the timing edge) and against
    ``hold`` (the opportunity cost). Fail-closed: unmeasurable is not a
    pass.
    """
    hold = hold_baseline(fwd, block)
    ent = _entries(mask, fwd, block)
    if len(ent) == 0:
        return {"hold": hold, "expected_gross": float("nan"), "nulls": [],
                "per_seed": [], "max_abs_t_vs_expected": float("nan"),
                "measured": False, "ok": False}

    # the mask, the finite cells and hence the selected (asset, block) slots
    # are identical under shuffling, so this expectation is the same for
    # every seed.
    mu = fwd.mean()                       # per-asset unconditional mean
    ent = ent.assign(mu=ent["asset"].map(mu))
    expected_by_block = ent.groupby("block")["mu"].mean()
    expected_gross = float(ent["mu"].mean())

    nulls, per_seed = [], []
    for s in seeds:
        shuffled = shuffle_within_columns(fwd, s)
        nulls.append(block_cluster_eval(mask, shuffled, block))
        ne = _entries(mask, shuffled, block)
        nm = ne.groupby("block")["fwd"].mean()
        common = nm.index.intersection(expected_by_block.index)
        d = (nm.loc[common] - expected_by_block.loc[common]).to_numpy(
            dtype=float)
        d = d[np.isfinite(d)]
        if len(d) < 3 or not d.std(ddof=1) > 0:
            t = float("nan")
        else:
            t = float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))
        per_seed.append({"seed": s, "t_vs_expected": t,
                         "excess_gross": float(d.mean()) if len(d) else
                         float("nan"), "blocks": int(len(d))})

    ts = [abs(x["t_vs_expected"]) for x in per_seed
          if np.isfinite(x["t_vs_expected"])]
    measured = len(ts) == len(per_seed) and bool(ts)
    worst = max(ts) if ts else float("nan")
    return {"hold": hold, "expected_gross": expected_gross, "nulls": nulls,
            "per_seed": per_seed, "max_abs_t_vs_expected": worst,
            "measured": measured,
            "ok": bool(measured and worst < max_abs_t)}
