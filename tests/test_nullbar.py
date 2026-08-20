"""Tests written against known answers and failure modes, not against the
implementation. Several encode production bugs this library exists to
prevent — if a refactor reintroduces one, its test fails."""
from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

from unittest import mock

import numpy as np
import pandas as pd
import pytest

import nullbar
from nullbar import (AlreadySpentError, AmbiguousConditionError,
                     BarMismatchError, LeakError,
                     Registration, SealBrokenError, TrialLedger,
                     assert_no_leak, block_cluster_eval, clustered_t, dsr,
                     expected_max_abs_t, expected_max_sharpe, fill_bracket,
                     hold_baseline, lint_source, null_control, null_verdict,
                     prefix_replay_check, psr, sharpe, shuffle_within_columns,
                     through_mask, touch_mask)

ROOT = Path(__file__).parent.parent


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

    def test_misaligned_series_raise_instead_of_pairing_positionally(self):
        # values from one filter paired with another filter's labels is a
        # wrong answer that looks right; pandas would have silently zipped.
        v = pd.Series([1.0, 2.0, 3.0], index=[0, 1, 2])
        c = pd.Series(["a", "b", "c"], index=[7, 8, 9])
        with pytest.raises(ValueError):
            clustered_t(v, c)

    def test_all_nan_clusters_do_not_inflate_t(self):
        # n counted every cluster label while the mean and the spread
        # skipped the empty ones, so t came out x sqrt(n_total/n_finite) —
        # the exact inflation this function exists to remove, arriving
        # through the input. Trailing rows with no forward return yet are
        # the ordinary way it happens.
        rng = np.random.default_rng(0)
        vals = rng.normal(0.3, 1.0, 30)
        real = clustered_t(pd.Series(vals), pd.Series(np.arange(30)))
        padded = clustered_t(
            pd.Series(np.concatenate([vals, np.full(30, np.nan)])),
            pd.Series(np.arange(60)))
        assert padded[2] == real[2] == 30
        assert padded[0] == pytest.approx(real[0], rel=1e-12)

    def test_null_cluster_labels_raise(self):
        # pandas would drop these observations silently
        with pytest.raises(ValueError):
            clustered_t(pd.Series([1.0, 2.0, 3.0]),
                        pd.Series(["a", None, "b"]))

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            clustered_t(np.array([1.0, 2.0, 3.0]), np.array([0, 1]))


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

    def test_dsr_refuses_unknown_spread(self):
        # the other half of "unmeasured is not a verdict": both demos used
        # to invent sr_variance rather than record it
        assert dsr(0.1, n=500, n_trials=64, sr_variance=None) is None

    def test_dsr_deflates_with_trial_count(self):
        d1 = dsr(0.1, n=500, n_trials=1, sr_variance=0.002)
        d64 = dsr(0.1, n=500, n_trials=64, sr_variance=0.002)
        assert d64 < d1

    def test_expected_max_sharpe_grows_with_trials(self):
        a = expected_max_sharpe(2, 0.01)
        b = expected_max_sharpe(64, 0.01)
        assert 0 < a < b

    def test_sharpe_ignores_nans(self):
        assert np.isfinite(sharpe([0.1, np.nan, -0.05, 0.2, 0.05]))


class TestExpectedMaxAbsT:
    def test_matches_known_normal_anchors(self):
        # analytic anchors for iid standard-normal cells: E[max|Z|] of 1 is
        # E|Z| = sqrt(2/pi) ~ 0.798; 64 cells ~ 2.66 — the threshold that
        # swallowed a t of 2.68 in production. Monotone in between.
        assert expected_max_abs_t(1) == pytest.approx(np.sqrt(2 / np.pi),
                                                      abs=0.02)
        assert expected_max_abs_t(64) == pytest.approx(2.66, abs=0.08)
        assert (expected_max_abs_t(4) < expected_max_abs_t(16)
                < expected_max_abs_t(64))

    def test_t_tails_raise_the_bar_on_few_clusters(self):
        # the normal approximation flatters: real cluster-level t on 11
        # clusters has fatter tails, so the luck threshold is HIGHER.
        normal = expected_max_abs_t(16)
        few = expected_max_abs_t(16, df=10)         # 11 clusters
        assert few > normal * 1.10                  # audited at ~+18%

    def test_converges_to_the_normal_in_the_large_sample_limit(self):
        assert expected_max_abs_t(16, df=5000) == pytest.approx(
            expected_max_abs_t(16), rel=0.02)

    def test_seeded_and_finite_for_a_large_search(self):
        # chunked over the simulation axis: 2000 cells used to allocate
        # n_sims x n_cells in one array (~1.6 GB at 1000 cells).
        a = expected_max_abs_t(2000, n_sims=500)
        b = expected_max_abs_t(2000, n_sims=500)
        assert np.isfinite(a) and a == b

    def test_mean_is_refused_where_the_tails_own_it(self):
        # at 3 clusters the t tails dominate the mean of max|t| and the
        # "threshold" stops being one anything could pass
        with pytest.raises(ValueError, match="clusters"):
            expected_max_abs_t(16, df=2)
        assert expected_max_abs_t(16, df=6) > 0          # df>=5 is fine

    def test_median_is_available_when_the_mean_is_not(self):
        med = expected_max_abs_t(16, df=2, summary="median")
        q95 = expected_max_abs_t(16, df=2, summary=0.95)
        assert np.isfinite(med) and med > expected_max_abs_t(16)
        assert q95 > med                                  # a fatter bar

    def test_summary_must_be_something_it_can_compute(self):
        for bad in ("q95", 1.5, 0.0):
            with pytest.raises(ValueError):
                expected_max_abs_t(16, summary=bad)

    def test_docs_quote_the_function_they_cite(self):
        # the cheat sheet in docs/workflow.md said 1.6 / 2.2 / 2.7 while the
        # function it names returns 1.47 / 2.08 / 2.60. One shipped artifact
        # has to be wrong; this test decides which.
        rows = re.findall(
            r"\|\s*(\d+) cells?\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|",
            (ROOT / "docs" / "workflow.md").read_text())
        assert len(rows) >= 4, "cheat-sheet table not found"
        for cells, mean_q, tail_q in rows:
            k = int(cells)
            assert float(mean_q) == pytest.approx(
                expected_max_abs_t(k), abs=0.02), \
                f"docs say E[max] {mean_q} for {cells} cells"
            assert float(tail_q) == pytest.approx(
                expected_max_abs_t(k, summary=0.95), abs=0.02), \
                f"docs say 5% tail {tail_q} for {cells} cells"

    def test_cheat_sheet_cites_only_calls_it_prints(self):
        # The numeric check above compares the TABLE to the function, so a
        # stale sentence describing a column that is no longer there passes
        # it — which happened: the v0.3.0 text "column 3 is
        # expected_max_abs_t(k, df=20)" survived under a table whose third
        # column had become the 95th percentile. Every call the section
        # names must therefore produce a number the section prints.
        doc = (ROOT / "docs" / "workflow.md").read_text()
        section = doc[doc.index("### The deflation cheat sheet"):]
        rows = re.findall(
            r"\|\s*(\d+) cells?\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|",
            section)
        cells = [int(c) for c, _, _ in rows]
        printed = {float(x) for _, a, b in rows for x in (a, b)}

        def parse(arg: str):
            arg = arg.strip()
            if arg.startswith(("'", '"')):
                return arg.strip("'\"")
            return float(arg) if "." in arg else int(arg)

        for call in re.findall(r"expected_max_abs_t\(([^)]*)\)", section):
            parts = [a for a in call.split(",") if a.strip()]
            if not parts:
                continue
            first, kwargs = parts[0].strip(), {}
            for extra in parts[1:]:
                name, _, value = extra.partition("=")
                kwargs[name.strip()] = parse(value)
            counts = cells if first in ("k", "n_cells") else [int(first)]
            for n in counts:
                got = expected_max_abs_t(n, **kwargs)
                assert any(abs(got - q) <= 0.02 for q in printed), (
                    f"the cheat sheet cites expected_max_abs_t({call}) "
                    f"-> {got:.2f} at {n} cells, a number it never prints")

    def test_the_mean_is_a_coin_flip_not_a_bar(self):
        # pure noise beats its own expected maximum ~45% of the time, which
        # is why the cheat sheet's bar column is the 95th percentile
        rng = np.random.default_rng(0)
        best = np.abs(rng.standard_normal((50_000, 64))).max(axis=1)
        assert 0.40 < (best > expected_max_abs_t(64)).mean() < 0.50
        assert (best > expected_max_abs_t(64, summary=0.95)).mean() \
            == pytest.approx(0.05, abs=0.01)


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

    def test_dedupes_against_rows_another_writer_appended(self, tmp_path):
        # the in-memory hash cache must not blind a ledger to the file
        p = tmp_path / "trials.jsonl"
        a, b = TrialLedger(p), TrialLedger(p)
        a.record("x", {"i": 1})
        b.record("x", {"i": 1})                 # same cell, other instance
        assert TrialLedger(p).count() == 1

    def test_feeds_dsr_without_inventing_a_spread(self, tmp_path):
        # the design gap: dsr needs sr_variance and the ledger had no
        # metrics, so both shipped demos faked it.
        led = TrialLedger(tmp_path / "t.jsonl")
        assert led.sr_variance() is None                  # nothing recorded
        for i, sr in enumerate([0.01, 0.05, 0.02, 0.03]):
            led.record("cell", {"i": i}, metrics={"sr": sr})
        assert led.sr_variance() == pytest.approx(
            float(np.var([0.01, 0.05, 0.02, 0.03], ddof=1)))
        assert dsr(0.05, n=500, n_trials=led.count(),
                   sr_variance=led.sr_variance()) is not None

    def test_one_recorded_sharpe_is_not_a_spread(self, tmp_path):
        led = TrialLedger(tmp_path / "t.jsonl")
        led.record("cell", {"i": 0}, metrics={"sr": 0.02})
        assert led.sr_variance() is None
        assert dsr(0.05, n=500, n_trials=1,
                   sr_variance=led.sr_variance()) is None


# ── registration ─────────────────────────────────────────────────────────────
class TestRegistration:
    def _reg(self):
        return Registration(
            name="x", hypothesis="h", design={"hold": 24},
            bar={"t3": "clustered t >= 3.0", "beats_rule": "net > rule net"})

    def test_a_registration_keeps_its_own_name(self):
        # the bar-validation loop shadowed the `name` parameter, so every
        # registration froze itself under its LAST condition's name.
        r = Registration(name="mean-reversion-24h", hypothesis="h",
                         design={}, bar={"t3": "clustered t >= 3",
                                         "beats_hold": "net beats hold"})
        assert r.doc["name"] == "mean-reversion-24h"

    def test_the_name_survives_freezing(self, tmp_path):
        p = tmp_path / "reg.json"
        Registration(name="my-study", hypothesis="h", design={},
                     bar={"t3": "prose"}).freeze(p)
        assert json.loads(p.read_text())["name"] == "my-study"

    def test_bar_validation_still_names_the_offending_condition(self):
        with pytest.raises(ValueError, match="beats_hold"):
            Registration(name="s", hypothesis="h", design={},
                         bar={"beats_hold": {"metric": "net"}})

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

    def test_two_concurrent_looks_produce_exactly_one(self, tmp_path):
        """The one promise the whole library is built to keep.

        ``exists()`` then ``write_text()`` were two steps: forcing the
        interleaving produced TWO successes, and neither caller could tell.
        The barrier makes the race deterministic rather than hoping a
        scheduler reproduces it.
        """
        import threading
        p = tmp_path / "reg.json"
        self._reg().freeze(p)
        gate, wins, spent = threading.Barrier(2), [], []

        def spend(i):
            r = nullbar.Registration.load(p)
            original = r._stamp_path

            def at_the_same_moment(pp):
                s = original(pp)
                gate.wait(timeout=5)
                return s

            r._stamp_path = at_the_same_moment
            try:
                r.spend_test_look(p, {"t": float(i)})
                wins.append(i)
            except AlreadySpentError:
                spent.append(i)

        threads = [threading.Thread(target=spend, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(wins) == 1, f"{len(wins)} looks were spent, not one"
        assert len(spent) == 1
        # and the survivor is the one on disk, not a torn merge of both
        stamp = json.loads((tmp_path / "reg.test_look.json").read_text())
        assert stamp["results"]["t"] == float(wins[0])

    def test_an_unreadable_prior_stamp_still_refuses(self, tmp_path):
        # reporting WHEN the first look happened is a courtesy; a courtesy
        # that raises turns a correct refusal into a crash
        p = tmp_path / "reg.json"
        r = self._reg()
        r.freeze(p)
        (tmp_path / "reg.test_look.json").write_text("{not json")
        with pytest.raises(AlreadySpentError, match="unreadable"):
            r.spend_test_look(p, {"t": 99.0})

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


class TestVerdictFailsClosed:
    """S1: `conditions.get(k) is False` is identity against the singleton,
    so every naturally-computed condition sailed through as a pass."""

    def _reg(self):
        return Registration(name="x", hypothesis="h", design={},
                            bar={"t3": "clustered t >= 3.0"})

    def test_the_headline_number_fails_as_it_should(self):
        t = 2.42                                  # the real OOS result
        v = self._reg().verdict({"t3": t >= 3.0})  # np.False_ / bool
        assert v["pass"] is False and v["failed"] == ["t3"]

    def test_numpy_false_is_a_failure(self):
        v = self._reg().verdict({"t3": np.bool_(False)})
        assert v["pass"] is False and v["failed"] == ["t3"]

    def test_numpy_true_still_passes(self):
        assert self._reg().verdict({"t3": np.bool_(True)})["pass"] is True

    def test_pandas_scalar_comparison(self):
        s = pd.Series([2.42])
        assert self._reg().verdict({"t3": (s >= 3.0).all()})["pass"] is False
        assert self._reg().verdict({"t3": (s >= 2.0).all()})["pass"] is True

    @pytest.mark.parametrize("value", [None, 0, 1, "", "yes", float("nan"),
                                       np.float64(4.0), pd.NA])
    def test_non_boolean_conditions_fail_and_are_named(self, value):
        v = self._reg().verdict({"t3": value})
        assert v["pass"] is False
        assert v["failed"] == ["t3"] and "t3" in v["invalid"]

    def test_array_condition_raises_rather_than_guessing(self):
        with pytest.raises(AmbiguousConditionError):
            self._reg().verdict({"t3": np.array([True, False])})
        with pytest.raises(AmbiguousConditionError):
            self._reg().verdict({"t3": pd.Series([True, True])})


class TestTheSeal:
    """S3: the verdict graded memory, the stamp was bound to nothing."""

    def _frozen(self, tmp_path):
        r = Registration(name="x", hypothesis="h", design={"hold": 24},
                         bar={"t3": "clustered t >= 3.0"})
        p = tmp_path / "reg.json"
        return r, p, r.freeze(p)

    def test_lowering_the_bar_in_memory_is_refused(self, tmp_path):
        r, p, _ = self._frozen(tmp_path)
        r.doc["bar"]["t3"] = "clustered t >= 2.0"
        with pytest.raises(SealBrokenError):
            r.verdict({"t3": True})

    def test_editing_the_frozen_file_is_refused(self, tmp_path):
        r, p, _ = self._frozen(tmp_path)
        doc = json.loads(p.read_text())
        doc["bar"]["t3"] = "clustered t >= 2.0"
        p.write_text(json.dumps(doc, indent=2, sort_keys=True))
        with pytest.raises(SealBrokenError):
            r.verdict({"t3": True})

    def test_deleting_the_registration_is_refused(self, tmp_path):
        r, p, _ = self._frozen(tmp_path)
        p.unlink()
        with pytest.raises(SealBrokenError):
            r.verdict({"t3": True})

    def test_loaded_registration_grades_the_file(self, tmp_path):
        r, p, digest = self._frozen(tmp_path)
        v = Registration.load(p).verdict({"t3": True})
        assert v["verified"] is True and v["sha256"] == digest

    def test_unfrozen_registration_says_it_is_unverified(self):
        r = Registration(name="x", hypothesis="h", design={},
                         bar={"t3": "t >= 3"})
        assert r.verdict({"t3": True})["verified"] is False

    def test_stamp_is_bound_to_the_registration_hash(self, tmp_path):
        r, p, digest = self._frozen(tmp_path)
        r.spend_test_look(p, {"t": 2.42})
        stamp = json.loads((tmp_path / "reg.test_look.json").read_text())
        assert stamp["registration_sha256"] == digest
        status = r.seal_status(p)
        assert status["test_look_spent"] and status["stamp_bound"]

    def test_a_spent_look_is_visible_in_the_verdict(self, tmp_path):
        r, p, _ = self._frozen(tmp_path)
        assert r.verdict({"t3": True})["test_look_spent"] is False
        r.spend_test_look(p, {"t": 2.42})
        assert r.verdict({"t3": True})["test_look_spent"] is True

    def test_a_look_cannot_be_spent_on_a_moved_bar(self, tmp_path):
        r, p, _ = self._frozen(tmp_path)
        r.doc["bar"]["t3"] = "clustered t >= 2.0"
        with pytest.raises(SealBrokenError):
            r.spend_test_look(p, {"t": 2.42})


class TestBarSpecsAndBudget:
    """F9: the bar as written and the bar as evaluated could diverge — and
    did, in the flagship demo. F16: cells_budget was hashed into the seal
    and read by nothing."""

    def _reg(self, **kw):
        return Registration(
            name="x", hypothesis="h", design={},
            bar={"t3": {"metric": "t", "op": ">=", "value": 3.0},
                 "null_flat": {"metric": "null_t", "op": "<", "value": 3.0,
                               "abs": True},
                 "judgement": "a call only a human can make"}, **kw)

    def test_spec_grades_itself_from_results(self):
        v = self._reg().verdict(results={"t": 2.42, "null_t": -0.6},
                                conditions={"judgement": True})
        assert v["pass"] is False and v["failed"] == ["t3"]
        assert sorted(v["graded"]) == ["null_flat", "t3"]

    def test_abs_is_honoured(self):
        # |−4.0| < 3.0 is false; without abs it would pass
        v = self._reg().verdict(results={"t": 4.0, "null_t": -4.0},
                                conditions={"judgement": True})
        assert v["failed"] == ["null_flat"]

    def test_caller_disagreeing_with_the_frozen_bar_raises(self):
        with pytest.raises(BarMismatchError, match="t3"):
            self._reg().verdict(results={"t": 2.42, "null_t": 0.1},
                                conditions={"t3": True, "judgement": True})

    def test_caller_agreeing_is_fine(self):
        v = self._reg().verdict(results={"t": 4.0, "null_t": 0.1},
                                conditions={"t3": True, "judgement": True})
        assert v["pass"] is True

    def test_absent_metric_is_missing_not_false(self):
        v = self._reg().verdict(results={"null_t": 0.1},
                                conditions={"judgement": True})
        assert v["missing"] == ["t3"] and "t3" not in v["failed"]

    def test_nan_metric_fails(self):
        v = self._reg().verdict(results={"t": float("nan"), "null_t": 0.1},
                                conditions={"judgement": True})
        assert v["failed"] == ["t3"]

    def test_malformed_specs_are_refused_at_registration(self):
        with pytest.raises(ValueError):
            Registration("x", "h", {}, bar={"a": {"metric": "t", "op": ">="}})
        with pytest.raises(ValueError):
            Registration("x", "h", {},
                         bar={"a": {"metric": "t", "op": "=~", "value": 1}})
        with pytest.raises(TypeError):
            Registration("x", "h", {}, bar={"a": 3.0})

    def test_budget_is_enforced_when_the_trial_count_is_given(self):
        reg = self._reg(cells_budget=4)
        ok = reg.verdict(results={"t": 4.0, "null_t": 0.1},
                         conditions={"judgement": True}, n_trials=4)
        over = reg.verdict(results={"t": 4.0, "null_t": 0.1},
                           conditions={"judgement": True}, n_trials=9)
        assert ok["pass"] is True and ok["budget"]["ok"] is True
        assert over["pass"] is False and over["budget"] == {
            "registered": 4, "spent": 9, "ok": False}

    def test_budget_is_not_checked_unless_asked(self):
        v = self._reg(cells_budget=1).verdict(
            results={"t": 4.0, "null_t": 0.1}, conditions={"judgement": True})
        assert v["pass"] is True and v["budget"] is None


class TestRefreezing:
    """F11: created_at is in the hash, so re-running the same registration
    script after a crash accused the user of editing history."""

    def _make(self):
        return Registration(name="x", hypothesis="h", design={"hold": 24},
                            bar={"t3": "clustered t >= 3.0"})

    def test_identical_registration_re_runs_cleanly(self, tmp_path):
        p = tmp_path / "reg.json"
        first = self._make().freeze(p)
        again = self._make()                 # new created_at, same promise
        assert again.freeze(p) == first      # no FileExistsError
        assert again.verdict({"t3": True})["verified"] is True

    def test_a_moved_bar_is_still_refused(self, tmp_path):
        p = tmp_path / "reg.json"
        self._make().freeze(p)
        lowered = Registration(name="x", hypothesis="h", design={"hold": 24},
                               bar={"t3": "clustered t >= 2.0"})
        with pytest.raises(FileExistsError):
            lowered.freeze(p)


# ── evaluate ─────────────────────────────────────────────────────────────────
def _toy(seed=0, n=2000, k=6, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    fwd = pd.DataFrame(rng.normal(drift, 1.0, (n, k)), index=idx,
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

    def test_non_datetime_index_raises(self):
        # pd.to_datetime reads an integer index as nanoseconds since the
        # epoch: every row floored into one 1970 block, one cluster, no
        # error, a number that looks like a result.
        rng = np.random.default_rng(0)
        idx = pd.RangeIndex(400)
        fwd = pd.DataFrame({"A": rng.normal(0, 1, 400)}, index=idx)
        mask = pd.DataFrame({"A": rng.random(400) < 0.3}, index=idx)
        with pytest.raises(TypeError):
            block_cluster_eval(mask, fwd)

    def test_no_trades_still_answers_every_question(self):
        # a strategy that took nothing is exactly when a caller KeyErrors
        mask, fwd = _toy()
        r = block_cluster_eval(mask & False, fwd)
        assert r["trades"] == 0 and r["per_year"] == {}


class TestNullVerdict:
    """S6: the docs demanded a comparison the library did not implement, so
    a null's raw |t| got quoted as 'OK' next to an effect it exceeded."""

    def test_hold_baseline_is_the_unconditional_mean(self):
        _, fwd = _toy(drift=0.30, n=4000)
        assert hold_baseline(fwd)["gross"] == pytest.approx(0.30, abs=0.05)

    def test_drift_does_not_read_as_a_broken_pipeline(self):
        mask, fwd = _toy(drift=0.30, n=4000)
        nv = null_verdict(mask, fwd)
        assert max(abs(x["t"]) for x in nv["nulls"]) > 3.0   # vs zero: scary
        assert nv["ok"] and nv["measured"]                   # correctly: fine

    def test_reference_is_what_the_mask_holds_not_equal_weight(self):
        # known answer: asset A pays +3 unconditionally, B pays 0. A mask
        # that only ever holds A must be referenced against ~3, not against
        # the 1.5 an equal-weight basket pays. Referencing equal-weight
        # reported "pipeline broken" for a sound pipeline on real data.
        idx = pd.date_range("2024-01-01", periods=4000, freq="h", tz="UTC")
        rng = np.random.default_rng(11)
        fwd = pd.DataFrame({"A": rng.normal(3.0, 1.0, 4000),
                            "B": rng.normal(0.0, 1.0, 4000)}, index=idx)
        mask = pd.DataFrame(False, index=idx, columns=["A", "B"])
        mask.iloc[::7, 0] = True
        nv = null_verdict(mask, fwd)
        assert nv["expected_gross"] == pytest.approx(3.0, abs=0.15)
        assert nv["hold"]["gross"] == pytest.approx(1.5, abs=0.15)
        assert nv["ok"]

    def test_threshold_is_applied(self):
        mask, fwd = _toy(n=2000)
        assert null_verdict(mask, fwd, max_abs_t=0.0)["ok"] is False

    def test_unmeasurable_null_is_not_a_pass(self):
        mask, fwd = _toy(n=200)
        nv = null_verdict(mask & False, fwd)
        assert nv["measured"] is False and nv["ok"] is False


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

    def test_column_swap_raises_instead_of_reporting_another_asset(self):
        # S2: measured before the fix — a swap turned a true gross of 1.0
        # into 9.0, silently, in the module that exists to correct a 1.3x
        # overstatement.
        idx = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 6, "B": [100.0] * 6}, index=idx)
        low = pd.DataFrame({"A": [99.0] * 6, "B": [99.0] * 6}, index=idx)
        fwd = pd.DataFrame({"A": [1.0] * 6, "B": [9.0] * 6}, index=idx)
        mask = pd.DataFrame({"A": [True] * 6, "B": [False] * 6}, index=idx)
        assert fill_bracket(mask, limit, low, fwd)["touch"]["gross"] == 1.0
        with pytest.raises(ValueError):
            fill_bracket(mask, limit, low, fwd[["B", "A"]])

    def test_index_mismatch_raises(self):
        idx = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
        f = pd.DataFrame({"A": [1.0] * 6}, index=idx)
        m = pd.DataFrame({"A": [True] * 6}, index=idx)
        with pytest.raises(ValueError):
            fill_bracket(m, f, f, f.iloc[:4])

    def test_resting_asks_are_modelled_too(self):
        # mirror image of the bid case: an ask fills when the HIGH reaches
        # it, and bar 1's high never does
        idx = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 4}, index=idx)
        high = pd.DataFrame({"A": [101.0, 99.0, 101.0, 101.0]}, index=idx)
        low = pd.DataFrame({"A": [99.0, 101.0, 99.0, 99.0]}, index=idx)
        fwd = pd.DataFrame({"A": [1.0, 1.0, 1.0, np.nan]}, index=idx)
        mask = pd.DataFrame({"A": [True, True, True, False]}, index=idx)
        ask = fill_bracket(mask, limit, high, fwd, side="sell")
        bid = fill_bracket(mask, limit, low, fwd, side="buy")
        assert ask["touch"]["n"] == bid["touch"]["n"] == 2
        assert ask["through"]["n"] <= ask["touch"]["n"]
        # the sides are not interchangeable: a BID read against highs asks
        # whether the high fell below the bid — a different question with a
        # different answer (1, not 2)
        assert fill_bracket(mask, limit, high, fwd)["touch"]["n"] == 1

    def test_side_must_be_one_of_two(self):
        idx = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
        f = pd.DataFrame({"A": [100.0] * 3}, index=idx)
        with pytest.raises(ValueError, match="side"):
            touch_mask(f, f, side="short")

    def test_touch_mask_checks_its_own_pair(self):
        idx = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 6, "B": [100.0] * 6}, index=idx)
        with pytest.raises(ValueError):
            touch_mask(limit, limit[["B", "A"]])


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

    def test_hash_inside_a_string_does_not_hide_the_leak(self, tmp_path):
        # S7: splitting on '#' truncated at a string literal — a false
        # NEGATIVE in a leak detector
        src = tmp_path / "feat.py"
        src.write_text('label = "close # then"; x = df.shift(-1)\n')
        assert [h.line for h in lint_source([src])] == [1]

    def test_commented_out_code_is_not_flagged(self, tmp_path):
        src = tmp_path / "feat.py"
        src.write_text("z = 1  # df.shift(-1) was here\n")
        assert lint_source([src]) == []

    def test_suppression_comment(self, tmp_path):
        src = tmp_path / "feat.py"
        src.write_text("x = df.shift(-1)  # noqa: leak (label, not feature)\n")
        assert lint_source([src]) == []

    def test_directory_walk(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text("x = df.shift(-1)\n")
        (tmp_path / "pkg" / "b.py").write_text("y = 1\n")
        assert len(lint_source([tmp_path])) == 1

    def test_cli_exit_codes(self, tmp_path, capsys):
        from nullbar.leaklint import main
        clean, dirty = tmp_path / "c.py", tmp_path / "d.py"
        clean.write_text("y = 1\n")
        dirty.write_text("x = df.shift(-1)\n")
        assert main([str(clean)]) == 0
        assert main([str(dirty)]) == 1
        assert main([str(dirty), "--severity", "high"]) == 1
        assert main([str(dirty), "--exit-zero"]) == 0
        assert "shift" in capsys.readouterr().out

    def test_shapes_the_heuristics_used_to_miss(self, tmp_path):
        src = tmp_path / "feat.py"
        src.write_text(
            'a = df["col#1"].shift(-1)\n'                    # 1: '#' in string
            "c = df.shift(periods=-1)\n"                     # 2: keyword form
            "e = np.roll(arr, -1)\n"                         # 3
            'f = df.merge_asof(other, direction="forward")\n'  # 4
            "b = df.shift(-1)\n"                             # 5: always caught
            "g = y.iloc[i+1]\n"                              # 6: always caught
            "ok = df.shift(1)\n")                            # 7: honest
        assert sorted(h.line for h in lint_source([src])) == [1, 2, 3, 4, 5, 6]

    def test_prose_in_strings_is_not_code(self, tmp_path):
        # the other half of the '#'-in-a-string bug: a pattern lying
        # ENTIRELY inside a literal is documentation, not a leak — while a
        # pattern whose match merely ENDS in one (a string argument) is
        src = tmp_path / "feat.py"
        src.write_text(
            'msg = "never use .shift(-1) here"\n'                     # 1 no
            'a = df["col#1"].shift(-1)\n'                             # 2 yes
            'b = f"docs say .shift(-1)"\n'                            # 3 no
            'c = df.merge_asof(other, direction="forward")\n'         # 4 yes
            'd = df.fillna(method="bfill")\n'                         # 5 yes
            "def f():\n"
            '    """prose about .shift(-1) in a docstring"""\n'       # 7 no
            "    return df.shift(-1)\n")                              # 8 yes
        assert sorted(h.line for h in lint_source([src])) == [2, 4, 5, 8]

    def test_missing_path_is_an_error_not_a_traceback(self, tmp_path,
                                                      capsys):
        from nullbar.leaklint import main
        missing = tmp_path / "nope.py"
        with pytest.raises(FileNotFoundError) as e:
            lint_source([missing])
        # a sentence, not an errno dump leaking from an open() deep inside
        assert str(e.value).startswith("no such file or directory:")
        assert main([str(missing)]) == 2
        assert "nullbar-lint:" in capsys.readouterr().err

    def test_lint_hit_carries_what_a_reviewer_needs(self, tmp_path):
        src = tmp_path / "feat.py"
        src.write_text("x = df.shift(-1)\n")
        hit, = lint_source([src])
        assert hit.line == 1 and hit.severity == "high"
        assert hit.text == "x = df.shift(-1)" and str(src) == hit.path
        assert "FUTURE" in hit.message

    def test_known_false_negatives_stay_documented(self):
        # The check is sound one way only. These two leaks are prefix-stable
        # by construction and DO pass — pinned here so the limitation cannot
        # quietly become a claim.
        idx = pd.date_range("2024-01-01", periods=300, freq="h")
        df = pd.DataFrame({"c": np.random.default_rng(6).normal(0, 1, 300)},
                          index=idx)
        MU, SD = df["c"].mean(), df["c"].std()       # fitted on everything
        prefit = lambda d: (d["c"] - MU) / SD
        closure = lambda d: df["c"].shift(-1).reindex(d.index)  # tomorrow
        assert prefix_replay_check(prefit, df)["leak"] is False
        assert prefix_replay_check(closure, df)["leak"] is False
        doc = prefix_replay_check.__doc__
        assert "CANNOT SEE" in doc and "fit-and-transform" in doc.lower()

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

    def test_bucket_boundary_alignment_cannot_hide_the_leak(self):
        # Regression: with len divisible by 24, the default cuts all land on
        # midnight, where the prefix's final day is complete and a daily-
        # bucket leak is invisible. The offset cuts must still catch it.
        idx = pd.date_range("2024-01-01", periods=1440, freq="h", tz="UTC")
        df = pd.DataFrame({"c": np.random.default_rng(2).normal(0, 1, 1440)
                           .cumsum() + 100}, index=idx)
        def leaky_mtf(d):
            daily = d["c"].resample("1D").last()
            return daily.reindex(d.index, method="ffill")
        assert prefix_replay_check(leaky_mtf, df)["leak"] is True

    def test_dropped_warmup_rows_are_not_a_leak(self):
        # S5: positional comparison made every warm-up-dropping feature a
        # crash ("operands could not be broadcast"), i.e. a false alarm on
        # correct code — and a leak checker that cries wolf gets disabled
        idx = pd.date_range("2024-01-01", periods=300, freq="h")
        df = pd.DataFrame({"c": np.random.default_rng(3).normal(0, 1, 300)},
                          index=idx)
        rep = prefix_replay_check(lambda d: d.rolling(24).mean().dropna(), df)
        assert rep["leak"] is False and rep["rows_compared"] > 0

    def test_non_numeric_features_are_compared_not_cast(self):
        idx = pd.date_range("2024-01-01", periods=300, freq="h")
        df = pd.DataFrame({"c": np.random.default_rng(4).normal(0, 1, 300)},
                          index=idx)
        causal = lambda d: pd.Series(np.where(d["c"] > 0, "up", "down"),
                                     index=d.index)
        leaky = lambda d: pd.Series(
            np.where(d["c"] > d["c"].mean(), "up", "down"), index=d.index)
        assert prefix_replay_check(causal, df)["leak"] is False
        assert prefix_replay_check(leaky, df)["leak"] is True

    def test_a_check_that_compared_nothing_is_not_a_pass(self):
        idx = pd.date_range("2024-01-01", periods=100, freq="h")
        df = pd.DataFrame({"c": np.arange(100.0)}, index=idx)
        rep = prefix_replay_check(lambda d: d.iloc[0:0], df)
        assert rep["leak"] is False and rep["checked"] is False
        with pytest.raises(LeakError):
            assert_no_leak(rep, "empty")

    def test_assert_no_leak_names_the_feature(self):
        idx = pd.date_range("2024-01-01", periods=200, freq="h")
        df = pd.DataFrame({"c": np.random.default_rng(5).normal(0, 1, 200)},
                          index=idx)
        with pytest.raises(LeakError, match="zscore"):
            assert_no_leak(prefix_replay_check(
                lambda d: (d - d.mean()) / d.std(), df), "zscore")


# ── exported surface that only ever ran through its callers ─────────────────
class TestExportedHelpers:
    def test_shuffle_preserves_each_columns_values(self):
        idx = pd.date_range("2024-01-01", periods=200, freq="h", tz="UTC")
        rng = np.random.default_rng(0)
        fwd = pd.DataFrame({"A": rng.normal(0, 1, 200),
                            "B": rng.normal(5, 1, 200)}, index=idx)
        out = shuffle_within_columns(fwd, seed=3)
        for col in fwd.columns:                      # a permutation, per column
            assert sorted(out[col]) == pytest.approx(sorted(fwd[col]))
        assert not out["A"].equals(fwd["A"])         # and actually shuffled
        assert out.index.equals(fwd.index)

    def test_shuffle_keeps_nan_positions(self):
        idx = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
        fwd = pd.DataFrame({"A": [1.0, np.nan, 3.0, np.nan, 5.0,
                                  6.0, 7.0, 8.0, 9.0, 10.0]}, index=idx)
        out = shuffle_within_columns(fwd, seed=1)
        assert out["A"].isna().tolist() == fwd["A"].isna().tolist()

    def test_through_requires_trading_past_the_bid(self):
        idx = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 3}, index=idx)
        # next bar's low touches exactly 100.0, never goes through it
        low = pd.DataFrame({"A": [100.0, 100.0, 100.0]}, index=idx)
        assert bool(touch_mask(limit, low).iloc[0, 0]) is True
        assert bool(through_mask(limit, low, margin=5e-4).iloc[0, 0]) is False

    def test_through_requires_trading_past_the_ask(self):
        # the mirror: an ask at 100 is "traded through" only above 100.05,
        # so the margin has to move the other way for side="sell"
        idx = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
        limit = pd.DataFrame({"A": [100.0] * 3}, index=idx)
        high = pd.DataFrame({"A": [100.0, 100.0, 100.0]}, index=idx)
        assert bool(touch_mask(limit, high, side="sell").iloc[0, 0]) is True
        assert bool(through_mask(limit, high, margin=5e-4,
                                 side="sell").iloc[0, 0]) is False


# ── packaging ────────────────────────────────────────────────────────────────
class TestPackaging:
    def test_version_matches_pyproject(self):
        txt = (ROOT / "pyproject.toml").read_text()
        assert f'version = "{nullbar.__version__}"' in txt

    def test_module_entry_point_runs(self, tmp_path):
        import subprocess
        import sys
        clean = tmp_path / "ok.py"
        clean.write_text("x = df.rolling(24).mean()\n")
        r = subprocess.run([sys.executable, "-m", "nullbar", str(clean)],
                           capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 0, r.stderr
        assert "0 hit(s)" in r.stdout

    def test_a_verdict_reads_the_frozen_file_once(self, tmp_path):
        from unittest import mock
        reg = Registration("x", "h", {}, bar={"t3": "prose"})
        path = tmp_path / "r.json"
        reg.freeze(path)
        real, seen = Path.read_text, []
        with mock.patch.object(Path, "read_text",
                               lambda self, *a, **k: (seen.append(str(self)),
                                                      real(self, *a, **k))[1]):
            reg.verdict({"t3": True})
        assert sum(1 for c in seen if c.endswith("r.json")) == 1

    def test_ships_type_information(self):
        assert (ROOT / "nullbar" / "py.typed").exists()

    def test_no_stale_references_to_the_old_name(self):
        # the package was renamed at 0.2.0; an import of the old name left
        # in a doc or an example is an install nobody can reproduce.
        # CHANGELOG.md is exempt: it has to name the old import to tell
        # people what to change.
        old = "pre" + "reg"          # spelled out so this file never self-matches
        pat = re.compile(rf"\b(import {old}\b|from {old}\b|{old}\.[a-z_]+\()")
        stale = []
        for p in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.md")):
            if ".git" in p.parts or "dist" in p.parts \
                    or p.name == "CHANGELOG.md":
                continue
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if pat.search(line):
                    stale.append(f"{p.relative_to(ROOT)}:{i}")
        assert not stale, f"stale imports of the old name: {stale}"


class TestTheLedgerIsAtomic:
    """Read, decide, append — three steps with two gaps in them.

    Two workers recording the same ``(name, params)`` both saw no row and
    both appended it, so an identical pair became two trials and the count
    every deflation figure divides by no longer described the search.
    """

    def test_the_decision_and_the_append_share_one_exclusive_lock(self,
                                                                   tmp_path):
        """A barrier cannot force this interleaving any more — the second
        thread blocks on the lock before it can reach the barrier, which is
        the point. So assert the STRUCTURE instead: the rows are read while
        an exclusive lock is held, and the row is written before it is
        released. A refactor that pulls them apart fails here.
        """
        led = nullbar.TrialLedger(tmp_path / "t.jsonl")
        led.record("warm", {"x": 0})
        events = []
        real_lock, real_read = led._lock, led._read_rows

        @contextlib.contextmanager
        def watched_lock(exclusive):
            events.append(f"acquire:{'EX' if exclusive else 'SH'}")
            with real_lock(exclusive) as handle:
                yield handle
            events.append("release")

        def watched_read():
            events.append("read")
            return real_read()

        led._lock, led._read_rows = watched_lock, watched_read
        led.record("under-the-lock", {"q": 1})

        assert events[0] == "acquire:EX", events
        assert events[-1] == "release", events
        assert "read" in events[1:-1], events
        # and nothing was released and re-taken in between
        assert events.count("acquire:EX") == 1 and events.count("release") == 1

    def test_concurrent_processes_lose_no_distinct_trial(self, tmp_path):
        # the lock must serialise, not swallow: an undercounted search is
        # the defect, and an over-serialised one that drops rows is worse
        import multiprocessing as mp
        path = tmp_path / "t.jsonl"
        with mp.Pool(8) as pool:
            pool.map(_record_distinct, [(str(path), i) for i in range(24)])
        rows = [json.loads(x) for x
                in path.read_text().splitlines() if x.strip()]
        assert len(rows) == 24
        assert nullbar.TrialLedger(path).count() == 24

    def test_concurrent_processes_collapse_an_identical_pair(self, tmp_path):
        import multiprocessing as mp
        path = tmp_path / "t.jsonl"
        with mp.Pool(8) as pool:
            pool.map(_record_same, [(str(path), i) for i in range(24)])
        rows = [json.loads(x) for x
                in path.read_text().splitlines() if x.strip()]
        assert len(rows) == 1, rows

    def test_a_forced_scan_does_not_trust_the_size_heuristic(self, tmp_path):
        """Size is a proxy for "changed", and two rows can serialise to the
        same length — so the one place correctness depends on a fresh read,
        deduplication under the write lock, does not get to trust a proxy.
        """
        path = tmp_path / "t.jsonl"
        led = nullbar.TrialLedger(path)
        led.record("a", {"q": 1})
        led.count()                                # prime the cache
        before = path.read_text()
        # a DIFFERENT row of exactly the same byte length
        replacement = before.replace('"name": "a"', '"name": "z"')
        assert replacement != before and len(replacement) == len(before)
        path.write_text(replacement)
        assert path.stat().st_size == led._size    # the heuristic sees nothing

        assert led._scan()[0]["name"] == "a"       # stale, and it cannot tell
        assert led._scan(force=True)[0]["name"] == "z"


def _record_distinct(args):
    path, i = args
    nullbar.TrialLedger(path).record("spread", {"q": i})


def _record_same(args):
    path, _ = args
    nullbar.TrialLedger(path).record("collide", {"q": 1})


class TestRecordReadsAreGuardedEverywhere:
    """Every path this package opens is, in the case that matters, supplied
    by somebody else — you clone a repository and run nullbar on what it
    ships. The first fix guarded the anchor's entry paths; the sidecar, the
    registration, the stamp and the ledger were all still read blind."""

    def test_a_registration_pointing_at_a_device_is_refused(self, tmp_path):
        p = tmp_path / "reg.json"
        p.symlink_to("/dev/zero")
        with pytest.raises(OSError, match="not an ordinary file"):
            nullbar.Registration.load(p)

    def test_a_ledger_pointing_at_a_device_is_refused(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.symlink_to("/dev/zero")
        with pytest.raises(OSError, match="not an ordinary file"):
            nullbar.TrialLedger(p).count()

    def test_an_oversized_record_is_refused_by_size(self, tmp_path):
        from nullbar import _records
        p = tmp_path / "big.json"
        p.write_text("x" * 4096)
        with mock.patch.object(_records, "MAX_RECORD_BYTES", 1024):
            with pytest.raises(OSError, match="over the"):
                _records.record_text(p, "record")

    def test_an_ordinary_record_is_unaffected(self, tmp_path):
        from nullbar import _records
        p = tmp_path / "ok.json"
        p.write_text('{"a": 1}')
        assert _records.record_text(p, "record") == '{"a": 1}'

    def test_a_symlink_to_a_REAL_file_still_works(self, tmp_path):
        # symlinks are an ordinary way to lay out a repo; what is refused
        # is what they point AT
        from nullbar import _records
        real = tmp_path / "real.json"
        real.write_text('{"a": 1}')
        link = tmp_path / "link.json"
        link.symlink_to(real)
        assert _records.record_text(link, "record") == '{"a": 1}'


class TestTheLockGuaranteeIsNeverSilentlyDropped:
    """A guarantee that evaporates on a platform the docs never excluded is
    worse than one that was never claimed. The first version ran unlocked
    wherever ``fcntl`` was missing and told nobody."""

    def test_no_locking_primitive_refuses_by_default(self, tmp_path):
        from nullbar import ledger as L
        with mock.patch.object(L, "HAVE_LOCKING", False):
            with pytest.raises(L.UnlockablePlatformError, match="deflation"):
                L.TrialLedger(tmp_path / "t.jsonl")

    def test_the_downgrade_must_be_asked_for_in_code(self, tmp_path):
        from nullbar import ledger as L
        with mock.patch.object(L, "HAVE_LOCKING", False):
            led = L.TrialLedger(tmp_path / "t.jsonl", require_lock=False)
            led.record("s", {"q": 1})
            assert led.count() == 1          # usable, just not concurrent-safe

    def test_windows_takes_the_msvcrt_path(self, tmp_path):
        """CI is ubuntu-only, so the Windows branch is exercised by driving
        the selection with fcntl absent and a stand-in msvcrt — the plumbing
        is covered even though the platform is not."""
        from nullbar import ledger as L
        calls = []

        class FakeMsvcrt:
            LK_LOCK, LK_UNLCK = 1, 0

            @staticmethod
            def locking(fd, mode, nbytes):
                calls.append(mode)

        with mock.patch.object(L, "fcntl", None), \
                mock.patch.object(L, "msvcrt", FakeMsvcrt), \
                mock.patch.object(L, "HAVE_LOCKING", True):
            led = L.TrialLedger(tmp_path / "t.jsonl")
            led.record("s", {"q": 1})
        assert calls == [FakeMsvcrt.LK_LOCK, FakeMsvcrt.LK_UNLCK], calls
        assert (tmp_path / "t.jsonl").read_text().count("\n") == 1

    def test_freezing_over_a_device_is_refused(self, tmp_path):
        # freeze() reads an existing file at the target to decide whether it
        # is the same registration; a planted symlink there is a read of
        # whatever it points at
        p = tmp_path / "reg.json"
        p.symlink_to("/dev/zero")
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        with pytest.raises(OSError, match="not an ordinary file"):
            reg.freeze(p)

    def test_sealing_against_a_device_is_refused(self, tmp_path):
        # _read backs seal_status and verdict, reachable with an explicit
        # reg_path that differs from the one the object was frozen at
        p = tmp_path / "reg.json"
        p.symlink_to("/dev/zero")
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        with pytest.raises(OSError, match="not an ordinary file"):
            reg.seal_status(p)

    def test_the_lock_does_not_open_the_path_before_checking_it(self,
                                                                tmp_path):
        """The guard was in ``_read_rows``, one step too late: acquiring the
        lock already did ``touch()`` and ``open("r+")`` on whatever the path
        pointed at. On a world-writable ``/dev/zero`` that happens to
        succeed and the refusal arrives correctly; on a stricter box it
        raises PermissionError from the lock setup instead. A guard whose
        answer depends on the permissions of a device node is not a guard.
        """
        p = tmp_path / "t.jsonl"
        p.symlink_to("/dev/zero")
        led = nullbar.TrialLedger(p)
        touched = []
        real_touch, real_open = Path.touch, Path.open

        def watched_touch(self, *a, **k):
            touched.append(("touch", str(self)))
            return real_touch(self, *a, **k)

        def watched_open(self, *a, **k):
            touched.append(("open", str(self)))
            return real_open(self, *a, **k)

        with mock.patch.object(Path, "touch", watched_touch), \
                mock.patch.object(Path, "open", watched_open):
            with pytest.raises(OSError, match="not an ordinary file"):
                led.count()
        assert not touched, f"opened the path before checking it: {touched}"

    def test_a_ledger_that_does_not_exist_yet_is_still_created(self,
                                                              tmp_path):
        # the guard must not stop the first record from creating the file
        led = nullbar.TrialLedger(tmp_path / "fresh.jsonl")
        led.record("s", {"q": 1})
        assert led.count() == 1

    def test_freeze_creates_exclusively(self, tmp_path):
        """``exists()``-then-write is two steps, so two callers freezing
        DIFFERENT designs at one path could both find nothing and both
        write, the second silently overwriting the first and losing the
        refusal freeze() exists to make.

        Asserted STRUCTURALLY rather than by racing two threads. The
        threaded version of this test patched a shared class attribute from
        both threads, so one thread's ``__exit__`` restored the original
        while the other was still inside — it passed here and failed in CI
        with a BrokenBarrierError. A flaky test is worse than no test.
        """
        # os.link is the primitive that is BOTH atomic and exclusive; the
        # property asserted here is exclusivity, and the mechanism moved
        # from open("x") to link() when atomicity was added.
        linked = []
        real_link = os.link

        def watched(src, dst, *a, **k):
            linked.append(str(dst))
            return real_link(src, dst, *a, **k)

        p = tmp_path / "reg.json"
        reg = nullbar.Registration(
            name="x", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        with mock.patch.object(os, "link", watched):
            reg.freeze(p)
        assert str(p) in linked, f"freeze did not publish by link: {linked}"
        # and the primitive really is exclusive: a second link to a taken
        # name fails rather than overwriting
        with pytest.raises(FileExistsError):
            os.link(__file__, str(p))

    def test_the_loser_of_the_race_is_refused(self, tmp_path):
        """What the losing caller experiences, deterministically: the file
        now exists and holds a different promise, so it is refused —
        exactly the answer it would have got had it arrived second."""
        p = tmp_path / "reg.json"
        nullbar.Registration(
            name="first", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1).freeze(p)
        with pytest.raises(FileExistsError, match="do not edit history"):
            nullbar.Registration(
                name="second", hypothesis="h", design={"v": 2},
                bar={"t": {"metric": "t", "op": ">=", "value": 9.0}},
                cells_budget=1).freeze(p)
        assert json.loads(p.read_text())["design"]["v"] == 1

    def test_refreezing_an_identical_design_is_still_accepted(self, tmp_path):
        # the retry path must not turn idempotence into a refusal
        p = tmp_path / "reg.json"
        for _ in range(2):
            nullbar.Registration(
                name="same", hypothesis="h", design={"v": 1},
                bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
                cells_budget=1).freeze(p)
        assert json.loads(p.read_text())["design"]["v"] == 1


class TestThePublicSurface:
    """A caller that wants to CATCH a refusal must be able to name it."""

    @pytest.mark.parametrize("name", ["RecordReadError",
                                      "UnlockablePlatformError"])
    def test_the_new_exceptions_are_importable(self, name):
        assert hasattr(nullbar, name), f"nullbar.{name} is not exported"
        assert name in nullbar.__all__

    def test_record_read_error_is_still_an_oserror(self):
        # existing handlers catch OSError; narrowing that would turn a
        # handled refusal into an unhandled crash for every current caller
        assert issubclass(nullbar.RecordReadError, OSError)

    def test_everything_in_all_actually_exists(self):
        assert [n for n in nullbar.__all__ if not hasattr(nullbar, n)] == []

    def test_a_dangling_symlink_is_refused_not_recursed(self, tmp_path):
        """``exists()`` follows the link and is False, so control reached
        the exclusive create — which fails, because the link itself is very
        much there. The retry found the same state and recursed until the
        stack ran out."""
        p = tmp_path / "reg.json"
        p.symlink_to(tmp_path / "nowhere.json")
        assert p.is_symlink() and not p.exists()
        reg = nullbar.Registration(
            name="x", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        with pytest.raises(FileExistsError, match="target does not exist"):
            reg.freeze(p)

    def test_a_symlink_to_a_real_path_still_freezes(self, tmp_path):
        # the guard must not refuse an ordinary layout
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        p = link / "reg.json"
        nullbar.Registration(
            name="x", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1).freeze(p)
        assert (real / "reg.json").exists()

    def test_the_retry_is_bounded(self, tmp_path):
        """A path that can neither be created nor read must REPORT, not
        recurse. Forced by making the exclusive create always fail while
        exists() stays False."""
        p = tmp_path / "reg.json"
        reg = nullbar.Registration(
            name="x", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        def always_taken(src, dst, *a, **k):
            raise FileExistsError(dst)

        # publication always fails while the name never appears: the only
        # honest outcome is to report, and the first version recursed
        with mock.patch.object(os, "link", always_taken):
            with pytest.raises(FileExistsError, match="cannot be created"):
                reg.freeze(p)

    def test_the_final_path_is_never_visible_half_written(self, tmp_path):
        """``open("x")`` gives exclusivity and NOT atomicity: the name
        appears the moment it is created, before a byte is written, so a
        concurrent reader saw an empty file where a registration should be
        and JSONDecodeError came out of the code deciding whether to accept
        a competing design.

        Asserted at the publication step rather than by racing threads —
        the threaded version of an earlier test patched a shared class
        attribute from both threads and was flaky in CI.
        """
        p = tmp_path / "reg.json"
        seen = {}
        real_link = os.link

        def watched(src, dst, *a, **k):
            # at the moment of publication: the real name must not exist,
            # and the source must already hold the whole document
            seen["dst_existed"] = Path(dst).exists()
            seen["src_payload"] = Path(src).read_text()
            return real_link(src, dst, *a, **k)

        with mock.patch.object(os, "link", watched):
            nullbar.Registration(
                name="x", hypothesis="h", design={"v": 1},
                bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
                cells_budget=1).freeze(p)

        assert seen["dst_existed"] is False
        assert json.loads(seen["src_payload"])["design"]["v"] == 1
        assert json.loads(p.read_text())["design"]["v"] == 1

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        p = tmp_path / "reg.json"
        nullbar.Registration(
            name="x", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1).freeze(p)
        assert [f.name for f in tmp_path.iterdir()] == ["reg.json"]

    def test_the_temp_file_is_cleaned_up_when_publication_fails(self,
                                                                tmp_path):
        p = tmp_path / "reg.json"
        nullbar.Registration(
            name="first", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1).freeze(p)
        with pytest.raises(FileExistsError):
            nullbar.Registration(
                name="second", hypothesis="h", design={"v": 2},
                bar={"t": {"metric": "t", "op": ">=", "value": 9.0}},
                cells_budget=1).freeze(p)
        assert [f.name for f in tmp_path.iterdir()] == ["reg.json"]

    @pytest.mark.parametrize("junk", ["", "{not json", "null", "[]"])
    def test_an_unreadable_existing_file_is_refused_clearly(self, tmp_path,
                                                            junk):
        # a concurrent writer can no longer leave one, but a crash, a
        # `touch`, or a file written by something else still can
        p = tmp_path / "reg.json"
        p.write_text(junk)
        reg = nullbar.Registration(
            name="x", hypothesis="h", design={"v": 1},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        with pytest.raises(FileExistsError):
            reg.freeze(p)
