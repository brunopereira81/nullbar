"""Tests for the report artifact.

House rule: assert on what a reader of the artifact would see, never by
re-deriving the value from the same expression the code uses. A test that
recomputes ``expected_max_abs_t`` and compares passes for any threshold the
implementation happens to produce.
"""
from __future__ import annotations

import json
import re

import pytest

import nullbar
from nullbar import cli
from nullbar.report import report_data


# ── fixtures ────────────────────────────────────────────────────────────────
SPEC_BAR = {
    "t3": {"metric": "t", "op": ">=", "value": 3.0},
    "net_positive": {"metric": "net", "op": ">", "value": 0.0},
}
FULL_RESULT = {"trades": 40, "clusters": 20, "gross": 0.5,
               "cluster_mean": 0.48, "t": 8.0, "per_year": {2024: 0.5}}


def _freeze(tmp_path, *, bar=SPEC_BAR, cells_budget=4, name="demo"):
    reg = nullbar.Registration(name=name, hypothesis="the demo predicts",
                               design={"hold_bars": 24}, bar=bar,
                               cells_budget=cells_budget)
    path = tmp_path / "reg.json"
    reg.freeze(path)
    return reg, path


def _ledger(tmp_path, n=4):
    led = nullbar.TrialLedger(tmp_path / "trials.jsonl")
    for i in range(n):
        led.record("demo", {"cell": i}, metrics={"sr": 0.1 + 0.05 * i})
    return led.path


def _record(tmp_path, *, results=None, bar=SPEC_BAR, cells_budget=4,
            trials=4, spend=True):
    reg, path = _freeze(tmp_path, bar=bar, cells_budget=cells_budget)
    if spend:
        reg.spend_test_look(path, results=results if results is not None
                            else nullbar.evidence(FULL_RESULT, net=0.4))
    return path, _ledger(tmp_path, trials)


# ── status is fail-closed ───────────────────────────────────────────────────
class TestStatus:
    def test_a_complete_satisfied_record_passes(self, tmp_path):
        path, led = _record(tmp_path)
        assert report_data(path, led)["verdict"]["status"] == "PASS"

    def test_a_sound_record_raises_no_findings(self, tmp_path):
        # findings are alarms; one that fires on a clean record trains the
        # reader to ignore the section.
        path, led = _record(tmp_path)
        assert report_data(path, led)["findings"] == []

    def test_no_test_look_is_incomplete_not_pass(self, tmp_path):
        path, led = _record(tmp_path, spend=False)
        data = report_data(path, led)
        assert data["verdict"]["status"] == "INCOMPLETE"
        assert any("no test look" in g for g in data["gaps"])

    def test_an_unmet_condition_fails(self, tmp_path):
        path, led = _record(tmp_path, results=nullbar.evidence(
            {**FULL_RESULT, "t": 1.0}, net=0.4))
        data = report_data(path, led)
        assert data["verdict"]["status"] == "FAIL"
        assert data["verdict"]["failed"] == ["t3"]

    def test_a_prose_condition_nobody_graded_is_incomplete(self, tmp_path):
        path, led = _record(tmp_path,
                            bar={**SPEC_BAR, "sane": "the operator agrees"})
        data = report_data(path, led)
        assert data["verdict"]["status"] == "INCOMPLETE"
        assert "sane" in data["verdict"]["missing"]

    def test_a_prose_condition_graded_by_the_researcher_counts(self, tmp_path):
        path, led = _record(
            tmp_path, bar={**SPEC_BAR, "sane": "the operator agrees"},
            results=nullbar.evidence(FULL_RESULT, net=0.4,
                                     conditions={"sane": True}))
        assert report_data(path, led)["verdict"]["status"] == "PASS"

    def test_a_disagreement_between_the_bar_and_its_grade_contradicts(
            self, tmp_path):
        # the spec says t >= 3 and t is 8, but the caller recorded False
        path, led = _record(tmp_path, results=nullbar.evidence(
            FULL_RESULT, net=0.4, conditions={"t3": False}))
        data = report_data(path, led)
        assert data["verdict"]["status"] == "CONTRADICTED"
        assert data["verdict"]["mismatch"]
        assert any("disagree" in f for f in data["findings"])

    def test_over_budget_search_fails_and_is_reported(self, tmp_path):
        path, led = _record(tmp_path, cells_budget=2, trials=7)
        data = report_data(path, led)
        assert data["verdict"]["status"] == "FAIL"
        assert data["trials"]["over_budget"] is True
        assert any("7 cells" in f and "budget of 2" in f
                   for f in data["findings"])


# ── the seal ────────────────────────────────────────────────────────────────
class TestSeal:
    def test_editing_the_registration_after_the_look_is_reported(
            self, tmp_path):
        path, led = _record(tmp_path)
        doc = json.loads(path.read_text())
        doc["design"]["hold_bars"] = 999          # edited behind the seal
        path.write_text(json.dumps(doc, indent=2, sort_keys=True))
        data = report_data(path, led)
        assert data["seal"]["stamp_bound"] is False
        assert any("NOT bound" in f for f in data["findings"])

    def test_a_look_stamped_before_the_registration_is_reported(
            self, tmp_path):
        path, led = _record(tmp_path)
        stamp_path = path.with_suffix(".test_look.json")
        stamp = json.loads(stamp_path.read_text())
        stamp["at"] = "1999-01-01T00:00:00+00:00"
        stamp_path.write_text(json.dumps(stamp))
        assert any("BEFORE the registration"
                   in f for f in report_data(path, led)["findings"])

    def test_an_unreadable_stamp_is_a_finding_not_a_crash(self, tmp_path):
        path, led = _record(tmp_path)
        path.with_suffix(".test_look.json").write_text("{not json")
        data = report_data(path, led)
        assert any("unreadable" in f for f in data["findings"])
        assert data["verdict"]["status"] != "PASS"

    def test_the_frozen_text_is_carried_verbatim(self, tmp_path):
        path, led = _record(tmp_path)
        assert report_data(path, led)["registration"]["frozen_text"] == \
            path.read_text()

    def test_a_missing_registration_says_so(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="nope.json"):
            report_data(tmp_path / "nope.json")


# ── the search and the deflation ────────────────────────────────────────────
class TestDeflation:
    def test_without_a_ledger_the_trial_count_is_a_gap(self, tmp_path):
        path, _ = _record(tmp_path)
        data = report_data(path)
        assert data["trials"]["count"] is None
        assert any("ledger" in g for g in data["gaps"])
        assert data["deflation"]["n_cells_source"].startswith("registered")

    def test_the_ledger_supplies_the_cell_count(self, tmp_path):
        path, led = _record(tmp_path, trials=6, cells_budget=9)
        d = report_data(path, led)["deflation"]
        assert (d["n_cells"], d["n_cells_source"]) == (6, "ledger")

    def test_degrees_of_freedom_come_from_the_clusters(self, tmp_path):
        path, led = _record(tmp_path)
        assert report_data(path, led)["deflation"]["df"] == \
            FULL_RESULT["clusters"] - 1

    def test_the_five_percent_line_sits_above_the_median(self, tmp_path):
        path, led = _record(tmp_path)
        d = report_data(path, led, sims=20_000)["deflation"]
        assert d["threshold_95"] > d["median"] > 0

    def test_the_bar_is_the_tail_and_not_the_expected_maximum(self, tmp_path):
        # the max|t| distribution is right-skewed, so its 95th percentile
        # must exceed its mean — this fails if the report ever quotes the
        # expected maximum, which noise clears ~45% of the time.
        path, led = _record(tmp_path)
        d = report_data(path, led, sims=20_000)["deflation"]
        assert d["threshold_95"] > nullbar.expected_max_abs_t(
            d["n_cells"], df=d["df"], n_sims=20_000, seed=0)

    def test_a_large_t_clears_the_line_and_a_small_one_does_not(
            self, tmp_path):
        big, led = _record(tmp_path)
        assert report_data(big, led, sims=20_000)["deflation"]["clears"]

    def test_a_small_t_does_not_clear(self, tmp_path):
        path, led = _record(tmp_path, results=nullbar.evidence(
            {**FULL_RESULT, "t": 0.4}, net=0.4))
        d = report_data(path, led, sims=20_000)["deflation"]
        assert d["observed_abs_t"] == 0.4 and d["clears"] is False

    def test_a_negative_t_is_compared_by_magnitude(self, tmp_path):
        path, led = _record(tmp_path, results=nullbar.evidence(
            {**FULL_RESULT, "t": -8.0}, net=0.4))
        assert report_data(path, led, sims=20_000)[
            "deflation"]["observed_abs_t"] == 8.0

    def test_deflated_sharpe_is_none_when_the_spread_is_unknown(
            self, tmp_path):
        path, _ = _record(tmp_path)
        led = nullbar.TrialLedger(tmp_path / "bare.jsonl")
        led.record("demo", {"cell": 1})              # no sr recorded
        data = report_data(path, led.path)
        assert data["deflation"]["dsr"] is None
        assert any("unmeasured" in g for g in data["gaps"])

    def test_deflated_sharpe_is_computed_when_the_ledger_supports_it(
            self, tmp_path):
        path, led = _record(tmp_path)
        d = report_data(path, led)["deflation"]
        assert 0.0 <= d["dsr"] <= 1.0 and "ledger" in d["dsr_source"]

    def test_a_recorded_deflated_sharpe_wins_over_recomputation(
            self, tmp_path):
        path, led = _record(tmp_path, results=nullbar.evidence(
            FULL_RESULT, net=0.4, dsr=0.123))
        d = report_data(path, led)["deflation"]
        assert (d["dsr"], d["dsr_source"]) == (0.123, "recorded")

    def test_thresholds_reproduce_across_runs(self, tmp_path):
        path, led = _record(tmp_path)
        a = report_data(path, led, sims=20_000)["deflation"]["threshold_95"]
        b = report_data(path, led, sims=20_000)["deflation"]["threshold_95"]
        assert a == b


# ── evidence() ──────────────────────────────────────────────────────────────
class TestEvidence:
    def test_it_derives_the_metric_the_bar_grades(self):
        null = {"max_abs_t_vs_expected": 1.2, "ok": True,
                "expected_gross": 0.01, "hold": {"gross": 0.02}}
        ev = nullbar.evidence({"t": 5.0}, null=null)
        assert ev["null_max_abs_t"] == 1.2
        assert ev["null_ok"] is True
        assert ev["hold_gross"] == 0.02

    def test_an_explicit_metric_beats_a_derived_one(self):
        ev = nullbar.evidence({"t": 5.0}, t=9.0)
        assert ev["t"] == 9.0

    def test_the_fill_haircut_is_touch_over_assumed(self):
        ev = nullbar.evidence(fills={"assumed": {"n": 2, "gross": 2.0},
                                     "touch": {"n": 1, "gross": 1.0}})
        assert ev["fill_haircut"] == 0.5

    def test_a_zero_assumed_gross_yields_no_haircut(self):
        ev = nullbar.evidence(fills={"assumed": {"n": 2, "gross": 0.0},
                                     "touch": {"n": 1, "gross": 1.0}})
        assert "fill_haircut" not in ev

    def test_sections_stay_out_of_the_metric_space(self):
        ev = nullbar.evidence({"t": 1.0}, fills={"assumed": {"gross": 1.0}})
        assert isinstance(ev["fills"], dict)

    def test_a_none_condition_stays_visible_instead_of_becoming_false(self):
        ev = nullbar.evidence(conditions={"sane": None})
        assert ev["conditions"]["sane"] != False        # noqa: E712
        assert isinstance(ev["conditions"]["sane"], str)

    def test_an_array_condition_is_refused(self):
        np = pytest.importorskip("numpy")
        with pytest.raises(nullbar.AmbiguousConditionError):
            nullbar.evidence(conditions={"sane": np.array([True, False])})

    def test_a_numpy_bool_condition_is_recorded_as_a_real_bool(self):
        np = pytest.importorskip("numpy")
        ev = nullbar.evidence(conditions={"sane": np.bool_(False)})
        assert ev["conditions"]["sane"] is False
        json.dumps(ev)                                  # must serialise

    def test_an_invalid_condition_fails_the_bar_it_grades(self, tmp_path):
        path, led = _record(
            tmp_path, bar={**SPEC_BAR, "sane": "the operator agrees"},
            results=nullbar.evidence(FULL_RESULT, net=0.4,
                                     conditions={"sane": None}))
        data = report_data(path, led)
        assert "sane" in data["verdict"]["invalid"]
        assert data["verdict"]["status"] == "FAIL"


# ── rendering ───────────────────────────────────────────────────────────────
class TestRender:
    def _html(self, tmp_path, **kw):
        path, led = _record(tmp_path, **kw)
        return nullbar.render_html(report_data(path, led, sims=20_000)), path

    def test_it_carries_the_hash_and_the_frozen_text(self, tmp_path):
        import html as _html
        page, path = self._html(tmp_path)
        data = report_data(path, tmp_path / "trials.jsonl")
        assert data["registration"]["sha256"] in page
        # the reader must be able to recover the exact frozen bytes from the
        # page and re-hash them; anything less makes the sha256 decorative.
        assert path.read_text() in _html.unescape(page)

    def test_the_banner_states_the_status(self, tmp_path):
        html, _ = self._html(tmp_path)
        assert re.search(r'class="banner PASS"', html)

    def test_an_incomplete_record_is_not_rendered_as_a_pass(self, tmp_path):
        html, _ = self._html(tmp_path, spend=False)
        assert re.search(r'class="banner INCOMPLETE"', html)
        assert "not a pass" in html

    def test_the_observed_value_is_shown_next_to_the_condition(self, tmp_path):
        page, _ = self._html(tmp_path)
        # in the bar table's own row, not merely somewhere on the page: a
        # PASS whose number lives three sections away is not evidence.
        row = re.search(r"<tr><td>t3</td>.*?</tr>", page, re.S).group(0)
        assert "8.0000" in row

    def test_every_recorded_metric_appears_somewhere(self, tmp_path):
        path, led = _record(tmp_path, results=nullbar.evidence(
            FULL_RESULT, net=0.4, private_note_metric=1.25))
        html = nullbar.render_html(report_data(path, led, sims=20_000))
        assert "private_note_metric" in html and "1.25" in html

    def test_a_hostile_name_cannot_inject_markup(self, tmp_path):
        reg = nullbar.Registration(name="<script>alert(1)</script>",
                                   hypothesis="h", design={}, bar=SPEC_BAR)
        path = tmp_path / "reg.json"
        reg.freeze(path)
        reg.spend_test_look(path, results=nullbar.evidence(FULL_RESULT,
                                                           net=0.4))
        html = nullbar.render_html(report_data(path))
        assert "<script>" not in html and "&lt;script&gt;" in html

    def test_it_is_self_contained(self, tmp_path):
        html, _ = self._html(tmp_path)
        assert "http://" not in html and "https://" not in html
        assert "<script" not in html

    def test_it_disclaims_being_a_track_record(self, tmp_path):
        html, _ = self._html(tmp_path)
        assert "not a track record" in html
        assert "tamper-<em>proof</em>" in html


# ── the CLI ─────────────────────────────────────────────────────────────────
class TestCLI:
    def test_it_writes_html_next_to_the_registration(self, tmp_path, capsys):
        path, led = _record(tmp_path)
        rc = cli.main(["report", str(path), "--ledger", str(led),
                       "--sims", "2000"])
        assert rc == 0
        out = path.with_suffix(".html")
        assert out.exists() and "<!doctype html>" in out.read_text()
        assert "PASS" in capsys.readouterr().out

    def test_a_non_passing_record_exits_nonzero(self, tmp_path):
        path, led = _record(tmp_path, spend=False)
        assert cli.main(["report", str(path), "--ledger", str(led),
                         "--sims", "2000"]) == 1

    def test_json_output_is_machine_readable(self, tmp_path):
        path, led = _record(tmp_path)
        out = tmp_path / "r.json"
        cli.main(["report", str(path), "--ledger", str(led), "--json",
                  "-o", str(out), "--sims", "2000"])
        assert json.loads(out.read_text())["verdict"]["status"] == "PASS"

    def test_a_missing_file_is_a_message_not_a_traceback(self, tmp_path,
                                                        capsys):
        rc = cli.main(["report", str(tmp_path / "gone.json")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "gone.json" in err and "Traceback" not in err

    def test_bare_paths_still_lint(self, tmp_path):
        src = tmp_path / "s.py"
        src.write_text("df['x'] = df['y'].shift(-1)\n")
        assert cli.main([str(src)]) == 1

    def test_the_lint_verb_works_too(self, tmp_path):
        src = tmp_path / "s.py"
        src.write_text("x = 1\n")
        assert cli.main(["lint", str(src)]) == 0

    def test_no_arguments_is_a_usage_error(self, capsys):
        assert cli.main([]) == 2
        assert "usage" in capsys.readouterr().err
