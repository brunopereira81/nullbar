"""Tests written against known answers and failure modes, not against the
implementation. Several encode production bugs this library exists to
prevent — if a refactor reintroduces one, its test fails."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prereg import (AlreadySpentError, Registration, TrialLedger,
                    block_cluster_eval, clustered_t, dsr, expected_max_abs_t,
                    expected_max_sharpe, fill_bracket, lint_source,
                    null_control, prefix_replay_check, psr, sharpe)


# ── stats ────────────────────────────────────────────────────────────────────
class TestClusteredT:
    def test_clustering_deflates_duplicated_observations(self):
        # 50 independent values vs the same values repeated 10x in-cluster:
        # pooled t inflates ~sqrt(10); clustered t must be IDENTICAL.
        rng = np.random.default_rng(0)
        vals = rng.normal(0.3, 1.0, 50)
        t1, _, n1 = clustered_t(pd.Series(vals), pd.Series(range(50)))
        rep = np.repeat(vals, 10)
        t2, _, n2 = clustered_t(pd.Series(rep),
                                pd.Series(np.repeat(range(50), 10)))
        assert n1 == n2 == 50
        assert t2 == pytest.approx(t1, rel=1e-12)

    def test_degenerate_returns_nan_not_infinity(self):
        t, m, n = clustered_t(pd.Series([1.0, 1.0]), pd.Series([0, 1]))
        assert np.isnan(t) and n == 2


class TestPSRDSR:
    def test_psr_known_value_zero_skill(self):
        # SR=0 vs benchmark 0 → PSR must be exactly 0.5
        assert psr(0.0, n=1000) == pytest.approx(0.5)

    def test_psr_unit_mixing_is_visible(self):
        # The 8760 trap, as it happened in production: a per-period observed
        # SR tested against an ANNUALIZED benchmark reads PSR ~ 0.000 forever;
        # the correct per-period benchmark gives a real number. Guard that the
        # trap is large, so unit discipline stays load-bearing.
        annual_threshold = 0.5
        wrong = psr(0.02, n=8760, benchmark_sr=annual_threshold)
        right = psr(0.02, n=8760,
                    benchmark_sr=annual_threshold / np.sqrt(8760))
        assert wrong < 0.001 and right > 0.5

    def test_dsr_refuses_unknown_trials(self):
        # unmeasured must never read as a verdict — the PSR=0.000 logs bug
        assert dsr(0.1, n=500, n_trials=None, sr_variance=0.01) is None

    def test_dsr_deflates_with_trial_count(self):
        d1 = dsr(0.1, n=500, n_trials=1, sr_variance=0.002)
        d64 = dsr(0.1, n=500, n_trials=64, sr_variance=0.002)
        assert d64 < d1

    def test_expected_max_sharpe_grows_with_trials(self):
        a = expected_max_sharpe(2, 0.01)
        b = expected_max_sharpe(64, 0.01)
        assert 0 < a < b

    def test_expected_max_abs_t_matches_known_anchors(self):
        # analytic anchors for iid standard-normal cells: E[max|Z|] of 1 is
        # E|Z| = sqrt(2/pi) ~ 0.798; 64 cells ~ 2.66 — the threshold that
        # swallowed a t of 2.68 in production. Monotone in between.
        assert expected_max_abs_t(1) == pytest.approx(np.sqrt(2 / np.pi),
                                                      abs=0.02)
        assert expected_max_abs_t(64) == pytest.approx(2.66, abs=0.08)
        assert (expected_max_abs_t(4) < expected_max_abs_t(16)
                < expected_max_abs_t(64))

    def test_sharpe_ignores_nans(self):
        assert np.isfinite(sharpe([0.1, np.nan, -0.05, 0.2, 0.05]))


# ── ledger ───────────────────────────────────────────────────────────────────
class TestLedger:
    def test_counts_distinct_trials_only(self, tmp_path):
        led = TrialLedger(tmp_path / "trials.jsonl")
        led.record("mr", {"th": 0.10})
        led.record("mr", {"th": 0.10})          # same cell re-run: 1 trial
        led.record("mr", {"th": 0.20})
        assert led.count() == 2

    def test_survives_reopen(self, tmp_path):
        p = tmp_path / "trials.jsonl"
        TrialLedger(p).record("a", {"x": 1})
        assert TrialLedger(p).count() == 1

    def test_no_delete_api(self):
        assert not any(m for m in dir(TrialLedger)
                       if "delete" in m or "remove" in m or "clear" in m)


# ── registration ─────────────────────────────────────────────────────────────
class TestRegistration:
    def _reg(self):
        return Registration(
            name="x", hypothesis="h", design={"hold": 24},
            bar={"t3": "clustered t >= 3.0", "beats_rule": "net > rule net"})

    def test_freeze_is_immutable(self, tmp_path):
        p = tmp_path / "reg.json"
        r = self._reg()
        h1 = r.freeze(p)
        assert r.freeze(p) == h1                 # idempotent
        r.doc["bar"]["t3"] = "t >= 2.0"          # try to lower the bar
        with pytest.raises(FileExistsError):
            r.freeze(p)

    def test_single_test_look(self, tmp_path):
        p = tmp_path / "reg.json"
        r = self._reg()
        r.freeze(p)
        r.spend_test_look(p, {"t": 0.61})
        with pytest.raises(AlreadySpentError):
            r.spend_test_look(p, {"t": 99.0})    # the "one more look"

    def test_verdict_requires_every_condition(self):
        r = self._reg()
        assert r.verdict({"t3": True, "beats_rule": True})["pass"]
        assert not r.verdict({"t3": True, "beats_rule": False})["pass"]
        v = r.verdict({"t3": True})              # silently dropping one
        assert not v["pass"] and v["missing"] == ["beats_rule"]

    def test_extra_conditions_cannot_rescue(self):
        r = self._reg()
        v = r.verdict({"t3": False, "beats_rule": True, "my_new_metric": True})
        assert not v["pass"]


# ── evaluate ─────────────────────────────────────────────────────────────────
def _toy(seed=0, n=2000, k=6):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    fwd = pd.DataFrame(rng.normal(0, 1.0, (n, k)), index=idx,
                       columns=[f"A{i}" for i in range(k)])
    mask = pd.DataFrame(rng.random((n, k)) < 0.1, index=idx,
                        columns=fwd.columns)
    return mask, fwd


class TestEvaluate:
    def test_one_entry_per_asset_per_block(self):
        mask, fwd = _toy()
        r = block_cluster_eval(mask, fwd, block="24h")
        # 6 assets x ~83 blocks bounds the trade count
        assert r["trades"] <= 6 * (2000 // 24 + 1)
        assert r["clusters"] <= 2000 // 24 + 1

    def test_planted_effect_is_found(self):
        mask, fwd = _toy()
        fwd2 = fwd + mask * 3.0                  # signal bars pay +3
        r = block_cluster_eval(mask, fwd2, block="24h")
        assert r["t"] > 5

    def test_null_control_flat_on_mean_zero_returns(self):
        mask, fwd = _toy()
        nulls = null_control(mask, fwd, seeds=(0, 1, 2, 3))
        assert max(abs(x["t"]) for x in nulls) < 3.0

    def test_null_preserves_marginal_not_signal(self):
        # Subtlety the docs must carry: shuffling preserves each asset's
        # marginal, so with a planted +3 on 10% of bars the null recovers
        # the ~+0.3 unconditional mean — NOT zero and NOT the +3 signal.
        # A null verdict therefore compares against the hold baseline.
        mask, fwd = _toy()
        fwd2 = fwd + mask * 3.0
        real = block_cluster_eval(mask, fwd2)
        nulls = null_control(mask, fwd2, seeds=(0, 1))
        for x in nulls:
            assert 0.05 < x["gross"] < 0.6      # ~ marginal mean, not 0/3
        assert real["gross"] > 2.5

    def test_axis_mismatch_raises(self):
        mask, fwd = _toy()
        with pytest.raises(ValueError):
            block_cluster_eval(mask.iloc[:, :3], fwd)


# ── fills ────────────────────────────────────────────────────────────────────
class TestFills:
    def test_missed_best_trades_shrink_gross(self):
        idx = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 6}, index=idx)
        # next-bar lows: bars 0,1 touched; bar 2's low stays above (missed)
        low = pd.DataFrame({"A": [99, 99, 99, 101, 99, 99]}, index=idx,
                           dtype=float)
        fwd = pd.DataFrame({"A": [1.0, 1.0, 5.0, 1.0, np.nan, np.nan]},
                           index=idx)  # the missed bar-2 entry was the +5
        mask = pd.DataFrame({"A": [True, True, True, True, False, False]},
                            index=idx)
        b = fill_bracket(mask, limit, low, fwd)
        assert b["assumed"]["n"] == 4
        assert b["touch"]["n"] == 3              # the +5 never filled
        assert b["touch"]["gross"] < b["assumed"]["gross"]

    def test_through_is_stricter_than_touch(self):
        idx = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 4}, index=idx)
        low = pd.DataFrame({"A": [99, 100.0, 99.98, 99]}, index=idx)
        fwd = pd.DataFrame({"A": [1.0, 1.0, 1.0, np.nan]}, index=idx)
        mask = pd.DataFrame({"A": [True, True, True, False]}, index=idx)
        b = fill_bracket(mask, limit, low, fwd, margin=5e-4)
        assert b["through"]["n"] <= b["touch"]["n"] <= b["assumed"]["n"]


# ── leaklint ─────────────────────────────────────────────────────────────────
class TestLeakLint:
    def test_static_patterns(self, tmp_path):
        src = tmp_path / "feat.py"
        src.write_text(
            "x = df.shift(-3)\n"
            "y = df.rolling(5, center=True).mean()\n"
            "z = df.rolling(5).mean()  # fine\n")
        hits = lint_source([src])
        assert {h.line for h in hits} == {1, 2}

    def test_prefix_replay_catches_full_sample_normalization(self):
        idx = pd.date_range("2024-01-01", periods=400, freq="h")
        df = pd.DataFrame({"c": np.random.default_rng(0).normal(0, 1, 400)},
                          index=idx)
        leaky = lambda d: (d - d.mean()) / d.std()     # uses the future
        causal = lambda d: d.rolling(24).mean()
        assert prefix_replay_check(leaky, df)["leak"] is True
        assert prefix_replay_check(causal, df)["leak"] is False

    def test_prefix_replay_catches_mtf_style_leak(self):
        # the two-year production leak in miniature: daily aggregate mapped
        # onto the hours INSIDE the day (each hour sees its own day's close)
        idx = pd.date_range("2024-01-01", periods=24 * 30, freq="h")
        df = pd.DataFrame({"c": np.random.default_rng(1).normal(0, 1, 720)
                           .cumsum() + 100}, index=idx)
        def leaky_mtf(d):
            daily = d["c"].resample("1D").last()
            return daily.reindex(d.index, method="ffill")  # same-day close
        assert prefix_replay_check(leaky_mtf, df)["leak"] is True
