"""Honest statistics for trading research.

Every function here exists because its naive version produced a wrong number
in production somewhere:

- ``clustered_t``: overlapping forward windows inflated a measured effect
  ~1.9x before clustering on time blocks corrected it.
- ``psr``/``dsr``: a gate fed an hourly-annualized Sharpe into a
  daily-calibrated PSR and logged "PSR=0.000" for months as though something
  had been measured. Everything here stays in PER-PERIOD units; annualize for
  display only.
- ``dsr`` REFUSES to run with an unknown trial count instead of returning
  0.0 — "unmeasured" and "zero" must never share a value.
- ``expected_max_abs_t``: a best-of-64 parameter search has a null max |t|
  of ~2.7; a t of 2.68 read as "almost significant" is exactly noise.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

_N = NormalDist()
_EULER_GAMMA = 0.5772156649015329


def clustered_t(values: pd.Series, clusters: pd.Series
                ) -> tuple[float, float, int]:
    """t-statistic with the CLUSTER (not the observation) as the unit of
    inference.

    Overlapping or same-period observations are not independent; pooling
    them overstates t. Group observations by cluster label (e.g. the 24h
    block an entry belongs to), average within clusters, and test the
    cluster means.

    Returns (t, cluster_mean, n_clusters); (nan, mean, n) when n < 3 or
    the spread is degenerate.
    """
    cl = pd.Series(np.asarray(values, dtype=float)).groupby(
        np.asarray(clusters)).mean()
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
    """
    if n < 2:
        return float("nan")
    denom = math.sqrt(max(1e-12,
        1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr ** 2))
    z = (observed_sr - benchmark_sr) * math.sqrt(n - 1) / denom
    return float(_N.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] across n_trials independent zero-skill trials.

    The benchmark a survivor of a search must beat (Bailey & LdP 2014).
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
        sr_variance: float, skew: float = 0.0, kurtosis: float = 3.0
        ) -> float | None:
    """Deflated Sharpe Ratio: PSR against the expected max of the search.

    ``n_trials`` is the TOTAL number of strategy variants evaluated in the
    search that produced this result — the number nobody wants to remember
    and a TrialLedger exists to record. Passing ``None`` returns ``None``
    (unmeasured), never 0.0: a verdict and a shrug must not be the same
    float.
    """
    if n_trials is None:
        return None
    bench = expected_max_sharpe(n_trials, sr_variance)
    return psr(observed_sr, n, benchmark_sr=bench, skew=skew,
               kurtosis=kurtosis)


def expected_max_abs_t(n_cells: int, n_sims: int = 100_000,
                       seed: int = 0) -> float:
    """Expected maximum |t| across n_cells independent null cells.

    The deflation threshold for a multi-cell design: a bar of 3.0 has real
    headroom over 4 cells (E[max|t|] ~ 2.2) and almost none over 64
    (~2.7). Simulated, seeded, standard-normal t approximation.
    """
    if n_cells < 1:
        raise ValueError("n_cells must be >= 1")
    rng = np.random.default_rng(seed)
    return float(np.abs(rng.standard_normal((n_sims, n_cells)))
                 .max(axis=1).mean())
