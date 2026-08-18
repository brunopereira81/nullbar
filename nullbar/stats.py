"""Honest statistics for trading research.

Every function here exists because its naive version produced a wrong number
in production somewhere:

- ``clustered_t``: overlapping forward windows inflated a measured effect
  ~1.9x before clustering on time blocks corrected it.
- ``psr``/``dsr``: a gate fed an hourly-annualized Sharpe into a
  daily-calibrated PSR and logged "PSR=0.000" for months as though something
  had been measured. Everything here stays in PER-PERIOD units; annualize for
  display only.
- ``dsr`` REFUSES to run with an unknown trial count or an unknown spread
  instead of returning 0.0 — "unmeasured" and "zero" must never share a
  value.
- ``expected_max_abs_t``: a best-of-64 parameter search has a null max |t|
  of ~2.6; a t of 2.68 read as "almost significant" is exactly noise.

Note on deflation and independence: ``expected_max_sharpe`` and
``expected_max_abs_t`` both assume INDEPENDENT trials. Real sweeps are
correlated (16 signals x 2 horizons x 2 directions is nowhere near 64
independent cells), so the threshold they return is too HIGH — deflation is
over-strict, which is the safe direction to be wrong in, but say so when
quoting it.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

_N = NormalDist()
_EULER_GAMMA = 0.5772156649015329
_SIM_CELLS_PER_CHUNK = 2_000_000        # bounds peak RSS at ~16 MB per chunk


def clustered_t(values, clusters) -> tuple[float, float, int]:
    """t-statistic with the CLUSTER (not the observation) as the unit of
    inference.

    Overlapping or same-period observations are not independent; pooling
    them overstates t. Group observations by cluster label (e.g. the 24h
    block an entry belongs to), average within clusters, and test the
    cluster means.

    Two pandas Series must share an index — they are NOT realigned and NOT
    paired positionally, because silently pairing a value with another
    row's cluster label is a wrong answer that looks like a right one.
    Arrays are paired positionally and must be the same length.

    Returns (t, cluster_mean, n_clusters); (nan, mean, n) when n < 3 or
    the spread is degenerate.
    """
    if isinstance(values, pd.Series) and isinstance(clusters, pd.Series):
        if not values.index.equals(clusters.index):
            raise ValueError(
                "values and clusters must share an index — pass "
                ".to_numpy() explicitly if positional pairing is intended")
    v = np.asarray(values, dtype=float)
    c = np.asarray(clusters)
    if len(v) != len(c):
        raise ValueError(f"length mismatch: {len(v)} values, {len(c)} "
                         "cluster labels")
    cl = pd.Series(v).groupby(c).mean()
    n = len(cl)
    if n < 3:
        return float("nan"), float(cl.mean()) if n else float("nan"), n
    sd = cl.std(ddof=1)
    if not sd > 0:
        return float("nan"), float(cl.mean()), n
    return float(cl.mean() / (sd / math.sqrt(n))), float(cl.mean()), n


def sharpe(returns: np.ndarray) -> float:
    """PER-PERIOD Sharpe (no annualization — see module docstring)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1))


def psr(observed_sr: float, n: int, benchmark_sr: float = 0.0,
        skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012).

    ALL Sharpe inputs are PER-PERIOD and ``n`` is the number of periods.
    Mixing an annualized SR with a per-period n (or vice versa) silently
    rescales the answer by sqrt(periods_per_year) — the exact bug that
    produced months of PSR=0.000 logs in the system this library was
    extracted from.

    ``kurtosis`` is NON-EXCESS (3.0 = Gaussian). pandas' ``.kurt()`` and
    scipy's ``kurtosis()`` return EXCESS by default — add 3. The radicand
    ``1 - skew*SR + (kurt-1)/4*SR^2`` cannot go negative for moments
    computed from real data (Pearson's inequality gives kurt >= skew^2 + 1);
    the clamp below only catches impossible hand-passed moments.
    """
    if n < 2:
        return float("nan")
    denom = math.sqrt(max(1e-12,
        1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr ** 2))
    z = (observed_sr - benchmark_sr) * math.sqrt(n - 1) / denom
    return float(_N.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] across n_trials INDEPENDENT zero-skill trials.

    The benchmark a survivor of a search must beat (Bailey & LdP 2014).
    ``sr_variance`` is the variance of the per-period Sharpe ACROSS the
    trials in the search — ``TrialLedger.sr_variance()`` if you recorded
    them.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    sd = math.sqrt(max(sr_variance, 1e-24))
    a = (1.0 - _EULER_GAMMA) * _N.inv_cdf(1.0 - 1.0 / n_trials)
    b = _EULER_GAMMA * _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(sd * (a + b))


def dsr(observed_sr: float, n: int, n_trials: int | None,
        sr_variance: float | None, skew: float = 0.0, kurtosis: float = 3.0
        ) -> float | None:
    """Deflated Sharpe Ratio: PSR against the expected max of the search.

    ``n_trials`` is the TOTAL number of strategy variants evaluated in the
    search that produced this result — the number nobody wants to remember
    and a TrialLedger exists to record. Passing ``None`` for the trial count
    OR for ``sr_variance`` returns ``None`` (unmeasured), never 0.0: a
    verdict and a shrug must not be the same float.
    """
    if n_trials is None or sr_variance is None:
        return None
    bench = expected_max_sharpe(n_trials, sr_variance)
    return psr(observed_sr, n, benchmark_sr=bench, skew=skew,
               kurtosis=kurtosis)


def expected_max_abs_t(n_cells: int, df: int | None = None,
                       n_sims: int = 100_000, seed: int = 0) -> float:
    """Expected maximum |t| across n_cells independent null cells.

    The deflation threshold for a multi-cell design: a bar of 3.0 has real
    headroom over 4 cells and almost none over 64.

    ``df`` is the degrees of freedom of ONE cell's t statistic — the number
    of clusters that cell was computed on, minus 1. Pass it. Cluster-level
    t statistics on 10-50 clusters have visibly fatter tails than a normal,
    and the normal approximation (``df=None``) therefore understates the
    luck threshold — by ~18% at 11 clusters, ~8% at 21, ~3% at 51 — in the
    FLATTERING direction, in the one function whose job is to be
    unflattering. ``df=None`` is the large-sample limit, and a lower bound.

    Simulated, seeded, and chunked over the simulation axis so a
    thousand-cell search does not allocate a gigabyte.
    """
    if n_cells < 1:
        raise ValueError("n_cells must be >= 1")
    if df is not None and df < 1:
        raise ValueError("df must be >= 1 (clusters - 1)")
    rng = np.random.default_rng(seed)
    per_chunk = max(1, _SIM_CELLS_PER_CHUNK // n_cells)
    total, done = 0.0, 0
    while done < n_sims:
        m = min(per_chunk, n_sims - done)
        draw = (rng.standard_normal((m, n_cells)) if df is None
                else rng.standard_t(df, (m, n_cells)))
        total += float(np.abs(draw).max(axis=1).sum())
        done += m
    return total / n_sims
