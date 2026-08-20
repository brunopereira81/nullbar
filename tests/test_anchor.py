"""Tests for git anchoring.

Every test drives a REAL repository through subprocess. Mocking git here
would test a model of git, and the whole claim of this module is about what
git actually does to history when someone rewrites it.
"""
from __future__ import annotations

import json
import subprocess

from pathlib import Path

import pytest
from unittest import mock

import nullbar
from nullbar import cli
from nullbar.anchor import GitError, verify_anchor
from nullbar.report import report_data

SPEC_BAR = {"t3": {"metric": "t", "op": ">=", "value": 3.0}}
RESULT = {"trades": 40, "clusters": 20, "gross": 0.5, "cluster_mean": 0.48,
          "t": 8.0, "per_year": {2024: 0.5}}


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), text=True,
                          capture_output=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Tester")
    (r / "README").write_text("seed\n")
    _git(r, "add", "README")
    _git(r, "commit", "-qm", "seed")
    return r


def _register(repo, name="study"):
    reg = nullbar.Registration(name=name, hypothesis="h", design={"hold": 24},
                               bar=SPEC_BAR, cells_budget=4)
    path = repo / "experiments" / f"{name}.json"
    reg.freeze(path)
    return reg, path


def _spend(reg, path, **kw):
    reg.spend_test_look(path, results=nullbar.evidence(RESULT, **kw))


class TestAnchoring:
    def test_it_records_the_commit_that_carries_the_registration(self, repo):
        reg, path = _register(repo)
        doc = nullbar.anchor(path, commit=True)
        entry = doc["entries"]["registration"]
        # the recorded commit must actually contain the file it claims
        touched = _git(repo, "show", "--name-only", "--format=",
                       entry["commit"])
        assert "experiments/study.json" in touched
        assert entry["path"] == "experiments/study.json"

    def test_the_anchor_record_is_committed_too(self, repo):
        # untracked, a clone carries no anchor and the third party this
        # exists for sees nothing
        _, path = _register(repo)
        nullbar.anchor(path, commit=True)
        tracked = _git(repo, "ls-files")
        assert "experiments/study.anchor.json" in tracked

    def test_it_refuses_to_anchor_an_uncommitted_file(self, repo):
        _, path = _register(repo)
        with pytest.raises(GitError, match="commit it first"):
            nullbar.anchor(path)                 # no --commit

    def test_commit_leaves_unrelated_staged_work_alone(self, repo):
        reg, path = _register(repo)
        (repo / "other.txt").write_text("work in progress\n")
        _git(repo, "add", "other.txt")
        nullbar.anchor(path, commit=True)
        # other.txt must still be staged and uncommitted
        assert "other.txt" in _git(repo, "diff", "--cached", "--name-only")

    def test_the_sidecar_lands_next_to_the_registration(self, repo):
        _, path = _register(repo)
        nullbar.anchor(path, commit=True)
        side = path.with_suffix(".anchor.json")
        assert side.exists()
        assert json.loads(side.read_text())["kind"] == "git"

    def test_anchoring_outside_a_repository_says_so(self, tmp_path):
        reg = nullbar.Registration(name="s", hypothesis="h", design={},
                                   bar=SPEC_BAR)
        path = tmp_path / "s.json"
        reg.freeze(path)
        with pytest.raises(GitError):
            nullbar.anchor(path, commit=True)

    def test_a_missing_registration_is_not_a_git_error(self, repo):
        with pytest.raises(FileNotFoundError):
            nullbar.anchor(repo / "nope.json")


class TestVerify:
    def _anchored(self, repo):
        reg, path = _register(repo)
        nullbar.anchor(path, commit=True, message="register")
        _spend(reg, path)
        nullbar.anchor(path, commit=True, message="test look")
        return path

    def test_an_intact_anchor_verifies(self, repo):
        assert verify_anchor(self._anchored(repo))["status"] == "intact"

    def test_the_registration_commit_precedes_the_test_look(self, repo):
        v = verify_anchor(self._anchored(repo))
        assert v["ordering"]["distinct"] and v["ordering"]["precedes"]

    def test_editing_the_frozen_file_afterwards_breaks_it(self, repo):
        path = self._anchored(repo)
        doc = json.loads(path.read_text())
        doc["bar"]["t3"]["value"] = 1.0              # lower the bar
        path.write_text(json.dumps(doc, indent=2, sort_keys=True))
        v = verify_anchor(path)
        assert v["status"] == "broken"
        assert any("not the registration that was committed" in f
                   for f in v["findings"])

    def test_a_rewritten_history_disagrees_with_a_kept_anchor(self, repo):
        # the attack this is for: the researcher rewrites history so the
        # tree looks consistent, but a reader (or the remote) still holds
        # the anchor naming the original commit.
        path = self._anchored(repo)
        side = path.with_suffix(".anchor.json")
        kept = side.read_text()
        reg_commit = json.loads(kept)["entries"]["registration"]["commit"]
        _git(repo, "reset", "-q", "--hard", f"{reg_commit}~1")
        reg = nullbar.Registration(                      # a friendlier bar
            name="study", hypothesis="h", design={"hold": 24},
            bar={"t3": {"metric": "t", "op": ">=", "value": 1.0}},
            cells_budget=4)
        reg.freeze(path)
        _git(repo, "add", "--", "experiments/study.json")
        _git(repo, "commit", "-qm", "as if it had always said this")
        side.write_text(kept)
        v = verify_anchor(path)
        assert v["status"] == "broken"
        assert v["entries"]["registration"]["in_history"] is False

    def test_a_rewind_that_takes_the_anchor_with_it_is_undetectable(self,
                                                                    repo):
        # THE limit of a local anchor, pinned rather than hidden: rewind far
        # enough and the anchor record itself reverts, so nothing is left to
        # disagree with anything. Only a copy the researcher does not
        # control — a push — closes this, which is why `witnessed` exists
        # and why the report says "local only" out loud.
        path = self._anchored(repo)
        look = json.loads(path.with_suffix(".anchor.json").read_text())
        _git(repo, "reset", "-q", "--hard",
             f"{look['entries']['test_look']['commit']}~1")
        v = verify_anchor(path)
        assert v["status"] == "intact"          # and it is telling the truth
        assert v["witnessed"] is False
        assert any("outside this machine" in n for n in v["notes"])

    def test_a_registration_committed_after_the_result_is_broken(self, repo):
        # the failure this whole module exists for: the study ran, the
        # result was recorded, and the "pre-registration" was committed
        # afterwards. Distinct commits, but in the wrong order.
        reg, path = _register(repo)
        _spend(reg, path)
        _git(repo, "add", "--", "experiments/study.test_look.json")
        _git(repo, "commit", "-qm", "the result")
        _git(repo, "add", "--", "experiments/study.json")
        _git(repo, "commit", "-qm", "the 'pre'-registration")
        nullbar.anchor(path)                     # records what git holds
        v = verify_anchor(path)
        assert v["ordering"]["distinct"] is True
        assert v["ordering"]["precedes"] is False
        assert v["status"] == "broken"
        assert any("does not precede" in f for f in v["findings"])

    def test_one_commit_for_both_carries_no_ordering(self, repo):
        reg, path = _register(repo)
        _spend(reg, path)
        nullbar.anchor(path, commit=True)            # both at once
        v = verify_anchor(path)
        assert v["ordering"]["distinct"] is False
        assert any("SAME commit" in n for n in v["notes"])

    def test_a_local_only_repository_is_flagged_as_unwitnessed(self, repo):
        v = verify_anchor(self._anchored(repo))
        assert v["witnessed"] is False
        assert any("outside this machine" in n for n in v["notes"])

    def test_a_pushed_commit_counts_as_witnessed(self, repo, tmp_path):
        remote = tmp_path / "remote.git"
        _git(repo, "init", "-q", "--bare", str(remote))
        path = self._anchored(repo)
        _git(repo, "remote", "add", "origin", str(remote))
        _git(repo, "push", "-q", "origin", "main")
        v = verify_anchor(path)
        assert v["witnessed"] is True
        assert v["entries"]["registration"]["remotes"]

    def test_no_sidecar_is_unanchored_not_intact(self, repo):
        _, path = _register(repo)
        assert verify_anchor(path)["status"] == "unanchored"

    def test_an_unreadable_sidecar_is_unverifiable_not_intact(self, repo):
        path = self._anchored(repo)
        path.with_suffix(".anchor.json").write_text("{not json")
        assert verify_anchor(path)["status"] == "unverifiable"

    def test_a_clone_at_another_path_verifies(self, repo, tmp_path):
        # the third party's actual workflow: clone, verify. The absolute
        # path recorded at anchor time is provenance, not a lookup key.
        self._anchored(repo)
        clone = tmp_path / "their-clone"
        _git(tmp_path, "clone", "-q", str(repo), str(clone))
        v = verify_anchor(clone / "experiments" / "study.json")
        assert v["status"] == "intact"
        assert v["witnessed"] is True

    def test_a_record_outside_any_repository_is_unverifiable(self, repo,
                                                             tmp_path):
        path = self._anchored(repo)
        loose = tmp_path / "loose"
        loose.mkdir()
        for suffix in (".json", ".test_look.json", ".anchor.json"):
            target = loose / f"study{suffix}"
            target.write_bytes(path.with_suffix(suffix).read_bytes())
        v = verify_anchor(loose / "study.json")
        assert v["status"] == "unverifiable"
        assert v["status"] != "intact"


class TestReportIntegration:
    def test_an_unanchored_record_names_the_gap(self, repo):
        reg, path = _register(repo)
        _spend(reg, path)
        data = report_data(path, sims=2000)
        assert data["anchor"]["status"] == "unanchored"
        assert any("not anchored" in g for g in data["gaps"])

    def test_an_intact_anchor_does_not_raise_the_gap(self, repo):
        reg, path = _register(repo)
        nullbar.anchor(path, commit=True)
        _spend(reg, path)
        nullbar.anchor(path, commit=True)
        data = report_data(path, sims=2000)
        assert data["anchor"]["status"] == "intact"
        assert not any("not anchored" in g for g in data["gaps"])

    def test_a_broken_anchor_contradicts_a_passing_bar(self, repo):
        reg, path = _register(repo)
        nullbar.anchor(path, commit=True)
        _spend(reg, path)
        nullbar.anchor(path, commit=True)
        # a ledger is REQUIRED for a 4-cell registration to reach PASS: the
        # deflation its bar was set against cannot be computed without the
        # trial count, so the status is INCOMPLETE until one is supplied.
        led = nullbar.TrialLedger(path.parent / "trials.jsonl")
        for i in range(4):
            led.record("study", {"cell": i}, metrics={"sr": 0.1 + 0.02 * i})
        assert report_data(path, led.path,
                           sims=2000)["verdict"]["status"] == "PASS"
        doc = json.loads(path.read_text())
        doc["bar"]["t3"]["value"] = 1.0
        path.write_text(json.dumps(doc, indent=2, sort_keys=True))
        data = report_data(path, led.path, sims=2000)
        # the conditions still "pass"; the record does not
        assert data["verdict"]["status"] == "CONTRADICTED"

    def test_the_report_states_what_a_git_anchor_cannot_prove(self, repo):
        reg, path = _register(repo)
        nullbar.anchor(path, commit=True)
        _spend(reg, path)
        nullbar.anchor(path, commit=True)
        page = nullbar.render_html(report_data(path, sims=2000))
        assert "wall-clock time" in page
        assert "ordering of documents is not ordering of knowledge" in page

    def test_local_only_is_stated_once_not_twice(self, repo):
        reg, path = _register(repo)
        nullbar.anchor(path, commit=True)
        _spend(reg, path)
        nullbar.anchor(path, commit=True)
        page = nullbar.render_html(report_data(path, sims=2000))
        assert page.count("outside this machine") == 1

    def test_an_unanchored_report_says_how_to_anchor(self, repo):
        reg, path = _register(repo)
        _spend(reg, path)
        page = nullbar.render_html(report_data(path, sims=2000))
        assert "nullbar anchor" in page


class TestAnchorCLI:
    def test_anchor_then_verify(self, repo, capsys):
        reg, path = _register(repo)
        assert cli.main(["anchor", str(path), "--commit"]) == 0
        _spend(reg, path)
        assert cli.main(["anchor", str(path), "--commit"]) == 0
        capsys.readouterr()
        assert cli.main(["verify", str(path)]) == 0
        assert "intact" in capsys.readouterr().out

    def test_verify_exits_nonzero_when_unanchored(self, repo, capsys):
        _, path = _register(repo)
        assert cli.main(["verify", str(path)]) == 1
        assert "unanchored" in capsys.readouterr().out

    def test_anchor_reports_a_git_failure_as_a_message(self, tmp_path,
                                                      capsys):
        reg = nullbar.Registration(name="s", hypothesis="h", design={},
                                   bar=SPEC_BAR)
        path = tmp_path / "s.json"
        reg.freeze(path)
        assert cli.main(["anchor", str(path)]) == 2
        err = capsys.readouterr().err
        assert "Traceback" not in err and err.strip()


class TestAnAnchorMustAnchorSomething:
    def test_a_sidecar_naming_no_registration_is_unverifiable(self, repo):
        # "intact" was returned for a sidecar naming nothing: the checks had
        # no work and every one passed vacuously
        _, path = _register(repo)
        path.with_suffix(".anchor.json").write_text(
            json.dumps({"entries": {}, "repo": str(repo)}))
        v = verify_anchor(path)
        assert v["status"] == "unverifiable"
        assert v["status"] != "intact"
        assert any("no registration entry" in f for f in v["findings"])

    def test_a_sidecar_with_only_a_test_look_entry_is_unverifiable(self, repo):
        _, path = _register(repo)
        path.with_suffix(".anchor.json").write_text(json.dumps(
            {"entries": {"test_look": {"path": "x", "commit": "0" * 40,
                                       "sha256": "y"}}, "repo": str(repo)}))
        assert verify_anchor(path)["status"] == "unverifiable"


class TestMalformedAnchorRecords:
    """A record that cannot be read is unverifiable — never a traceback."""

    def _repo(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        p = tmp_path / "z.json"
        p.write_text(json.dumps(
            {"name": "z", "hypothesis": "h", "design": {},
             "bar": {"t": {"metric": "t", "op": ">=", "value": 3.0}},
             "created_at": "2026-01-01T00:00:00+00:00", "cells_budget": 1}))
        return p

    @pytest.mark.parametrize("shape", [[], "abc", 7, None, {},
                                       {"commit": 123},
                                       {"commit": "abc"},
                                       {"path": "z.json"}])
    def test_a_malformed_entry_is_unverifiable_not_a_crash(self, tmp_path,
                                                           shape):
        # every entry is dereferenced with .get(); the empty-mapping fix
        # asked whether entries EXIST and never what shape they are, so
        # {"registration": []} raised AttributeError out of verification
        # AND out of report generation with it
        p = self._repo(tmp_path)
        (tmp_path / "z.anchor.json").write_text(json.dumps(
            {"kind": "git", "repo": str(tmp_path),
             "entries": {"registration": shape}}))
        out = nullbar.verify_anchor(p)
        assert out["status"] == "unverifiable"
        assert any("malformed" in f or "names no registration" in f
                   for f in out["findings"])

    def test_a_malformed_entry_does_not_crash_the_report(self, tmp_path):
        p = self._repo(tmp_path)
        reg = nullbar.Registration.load(p)
        (tmp_path / "z.test_look.json").write_text(json.dumps(
            {"at": "2026-02-01T00:00:00+00:00", "results": {"t": 5.0},
             "registration_sha256": reg.seal_status(p)["sha256"]}))
        (tmp_path / "z.anchor.json").write_text(json.dumps(
            {"kind": "git", "repo": str(tmp_path),
             "entries": {"registration": []}}))
        data = nullbar.report_data(p, sims=200)
        assert data["anchor"]["status"] == "unverifiable"

    def test_a_well_formed_entry_naming_a_dead_commit_is_broken(self,
                                                                tmp_path):
        # the shape check must not swallow the check it guards
        p = self._repo(tmp_path)
        (tmp_path / "z.anchor.json").write_text(json.dumps(
            {"kind": "git", "repo": str(tmp_path),
             "entries": {"registration": {"commit": "0" * 40,
                                          "path": "z.json"}}}))
        assert nullbar.verify_anchor(p)["status"] == "broken"


class TestTheLedgerIsAnchoredToo:
    """The bar and the number of cells it was set against are the two things
    a reader must be able to trust. The ledger carries the second, and it
    was the one file the anchor did not cover."""

    def _repo(self, tmp_path, trials=4, budget=40):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=budget)
        p = tmp_path / "r.json"
        reg.freeze(p)
        led = nullbar.TrialLedger(tmp_path / "r.jsonl")
        for i in range(trials):
            led.record("s", {"q": i})
        self._commit(tmp_path, ["r.json", "r.jsonl"], "freeze")
        reg.spend_test_look(p, results={"t": 5.0})
        self._commit(tmp_path, ["r.test_look.json"], "look")
        nullbar.anchor(p)
        self._commit(tmp_path, ["r.anchor.json"], "anchor")
        return p

    @staticmethod
    def _commit(repo, rels, msg):
        subprocess.run(["git", "add", *rels], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True)

    @staticmethod
    def _head(repo):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()

    def test_the_ledger_is_among_the_anchored_entries(self, tmp_path):
        p = self._repo(tmp_path)
        entries = json.loads((tmp_path / "r.anchor.json").read_text())["entries"]
        assert sorted(entries) == ["ledger", "registration", "test_look"]
        assert verify_anchor(p)["status"] == "intact"

    def test_a_shrunk_ledger_breaks_the_anchor(self, tmp_path):
        # the whole point: shrinking a ledger shrinks the deflation every
        # figure downstream is divided by, and nothing else in the record
        # disagrees with it
        p = self._repo(tmp_path)
        led = tmp_path / "r.jsonl"
        led.write_text(led.read_text().splitlines()[0] + "\n")
        out = verify_anchor(p)
        assert out["status"] == "broken"
        assert any("append-only record was rewritten" in f
                   for f in out["findings"])
        assert report_data(p, led, sims=200)["verdict"]["status"] \
            == "CONTRADICTED"

    def test_an_edited_ledger_row_breaks_the_anchor(self, tmp_path):
        # same row count, different content — a count check would miss it
        p = self._repo(tmp_path)
        led = tmp_path / "r.jsonl"
        rows = led.read_text().splitlines()
        rows[1] = json.dumps({"hash": "0" * 16, "name": "s",
                              "params": {"q": 999}, "note": "", "metrics": {},
                              "at": "2026-01-01T00:00:00+00:00"})
        led.write_text("\n".join(rows) + "\n")
        assert verify_anchor(p)["status"] == "broken"

    def test_appending_a_trial_is_legitimate_and_stays_intact(self, tmp_path):
        # append-only BY DESIGN — byte equality would break the moment
        # another cell is recorded, and a check that breaks gets turned off
        p = self._repo(tmp_path)
        nullbar.TrialLedger(tmp_path / "r.jsonl").record("s", {"q": 4242})
        assert verify_anchor(p)["status"] == "intact"

    def test_re_anchoring_after_new_commits_stays_intact(self, tmp_path):
        # the negative control for the backwards-move check: legitimate
        # re-anchoring only ever moves an entry FORWARD
        p = self._repo(tmp_path)
        nullbar.TrialLedger(tmp_path / "r.jsonl").record("s", {"q": 4242})
        self._commit(tmp_path, ["r.jsonl"], "one more trial")
        nullbar.anchor(p)
        assert verify_anchor(p)["status"] == "intact"

    def test_re_pointing_the_ledger_entry_breaks_the_anchor(self, tmp_path):
        """Aim the entry at a commit where the ledger was shorter, without
        also forging the hash. The sidecar names a commit AND what that
        commit was supposed to hold; re-pointing leaves the two disagreeing.
        """
        p = self._two_stage(tmp_path)
        early = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                               cwd=tmp_path, capture_output=True,
                               text=True).stdout.strip()
        side = tmp_path / "r.anchor.json"
        doc = json.loads(side.read_text())
        doc["entries"]["ledger"]["commit"] = early
        side.write_text(json.dumps(doc, indent=2))
        out = verify_anchor(p)
        assert out["status"] == "broken"
        assert any("records a different sha256" in f for f in out["findings"])

    def _two_stage(self, tmp_path):
        """A ledger that GREW across commits, so an earlier commit really
        does hold a shorter one."""
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=40)
        p = tmp_path / "r.json"
        reg.freeze(p)
        led = nullbar.TrialLedger(tmp_path / "r.jsonl")
        led.record("s", {"q": 0})
        self._commit(tmp_path, ["r.json", "r.jsonl"], "freeze")
        for i in range(1, 30):
            led.record("s", {"q": i})
        self._commit(tmp_path, ["r.jsonl"], "the search")
        reg.spend_test_look(p, results={"t": 5.0})
        self._commit(tmp_path, ["r.test_look.json"], "look")
        nullbar.anchor(p)
        self._commit(tmp_path, ["r.anchor.json"], "anchor")
        return p

    def test_the_full_tamper_chain_is_caught(self, tmp_path):
        """Shrink the ledger, re-point its entry at the commit that held the
        short version, and forge the recorded hash so the two agree. Every
        per-entry check then passes; only the sidecar's own committed copy
        contradicts it."""
        import hashlib
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=40)
        p = tmp_path / "r.json"
        reg.freeze(p)
        led = nullbar.TrialLedger(tmp_path / "r.jsonl")
        led.record("s", {"q": 0})
        self._commit(tmp_path, ["r.json", "r.jsonl"], "freeze")
        early, thin = self._head(tmp_path), (tmp_path / "r.jsonl").read_bytes()
        for i in range(1, 30):
            led.record("s", {"q": i})
        self._commit(tmp_path, ["r.jsonl"], "the search")
        reg.spend_test_look(p, results={"t": 5.0})
        self._commit(tmp_path, ["r.test_look.json"], "look")
        nullbar.anchor(p)
        self._commit(tmp_path, ["r.anchor.json"], "anchor")
        assert verify_anchor(p)["status"] == "intact"

        (tmp_path / "r.jsonl").write_bytes(thin)
        side = tmp_path / "r.anchor.json"
        doc = json.loads(side.read_text())
        doc["entries"]["ledger"]["commit"] = early
        doc["entries"]["ledger"]["sha256"] = hashlib.sha256(thin).hexdigest()
        side.write_text(json.dumps(doc, indent=2))
        out = verify_anchor(p)
        assert out["status"] == "broken"
        assert any("not a descendant" in f for f in out["findings"])

    def test_a_record_anchored_before_ledgers_were_covered_is_not_broken(
            self, tmp_path):
        # every record anchored before this existed would otherwise read as
        # tampered, which is false and teaches a reader to ignore the word
        p = self._repo(tmp_path)
        side = tmp_path / "r.anchor.json"
        doc = json.loads(side.read_text())
        del doc["entries"]["ledger"]
        side.write_text(json.dumps(doc, indent=2))
        self._commit(tmp_path, ["r.anchor.json"], "as it was before")
        out = verify_anchor(p)
        assert out["status"] == "intact"
        assert any("not covered by this anchor" in n for n in out["notes"])

    def test_an_uncommitted_ledger_is_recorded_as_uncovered(self, tmp_path):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=4)
        p = tmp_path / "r.json"
        reg.freeze(p)
        nullbar.TrialLedger(tmp_path / "r.jsonl").record("s", {"q": 1})
        self._commit(tmp_path, ["r.json"], "freeze")   # ledger NOT committed
        doc = nullbar.anchor(p)
        assert "ledger" not in doc["entries"]
        assert any("ledger" in u for u in doc["uncovered"])
        assert any("attested by nothing" in f
                   for f in verify_anchor(p)["findings"])


class TestALedgerNamedSomethingElse:
    """Coverage that depends on a filename fails silently when the name
    differs. nullbar's own walkthrough writes `trials.jsonl` beside
    `mr24.json`, and the first version of ledger anchoring covered nothing
    at all there while still reporting an intact anchor."""

    def _repo(self, tmp_path, ledger_name):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=40)
        p = tmp_path / "mr24.json"
        reg.freeze(p)
        led = nullbar.TrialLedger(tmp_path / ledger_name)
        for i in range(4):
            led.record("s", {"q": i})
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "freeze"], cwd=tmp_path,
                       check=True)
        reg.spend_test_look(p, results={"t": 5.0})
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "look"], cwd=tmp_path,
                       check=True)
        return p, tmp_path / ledger_name

    def test_an_explicit_ledger_is_covered(self, tmp_path):
        p, led = self._repo(tmp_path, "trials.jsonl")
        doc = nullbar.anchor(p, ledger=led)
        assert doc["entries"]["ledger"]["path"] == "trials.jsonl"
        assert verify_anchor(p)["status"] == "intact"

    def test_an_explicitly_covered_ledger_is_tamper_evident(self, tmp_path):
        p, led = self._repo(tmp_path, "trials.jsonl")
        nullbar.anchor(p, ledger=led)
        led.write_text(led.read_text().splitlines()[0] + "\n")
        assert verify_anchor(p)["status"] == "broken"

    def test_a_ledger_the_anchor_missed_is_named_by_the_report(self,
                                                               tmp_path):
        # the anchor cannot know it missed one; the report knows BOTH which
        # ledger was counted and which files were attested
        p, led = self._repo(tmp_path, "trials.jsonl")
        nullbar.anchor(p)                       # no --ledger: misses it
        assert "ledger" not in nullbar.verify_anchor(p)["entries"]
        data = report_data(p, led, sims=200)
        assert any("is not among the files this anchor covers" in g
                   for g in data["gaps"])

    def test_the_sibling_convention_still_needs_no_argument(self, tmp_path):
        p, led = self._repo(tmp_path, "mr24.jsonl")
        nullbar.anchor(p)
        assert "ledger" in nullbar.verify_anchor(p)["entries"]
        assert not any("not among the files this anchor covers" in g
                       for g in report_data(p, led, sims=200)["gaps"])


class TestAbsenceIsNotInnocence:
    """A check that does not run is not a check that passed.

    Four defects shipped in one week with this exact shape: an empty bar
    graded every result as PASS, a missing trial ledger left the deflation
    uncomputed and the verdict green, an empty ``entries`` mapping verified
    as intact, and — here — an entry DELETED from the working sidecar was
    skipped by the loop that was supposed to check it. Every one of them was
    a place where the absence of evidence read as evidence of absence of a
    problem.
    """

    def _anchored(self, tmp_path):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=4)
        p = tmp_path / "r.json"
        reg.freeze(p)
        led = nullbar.TrialLedger(tmp_path / "r.jsonl")
        for i in range(4):
            led.record("s", {"q": i})
        self._commit(tmp_path, "freeze")
        reg.spend_test_look(p, results={"t": 5.0})
        self._commit(tmp_path, "look")
        nullbar.anchor(p, ledger=tmp_path / "r.jsonl")
        self._commit(tmp_path, "anchor")
        return p

    @staticmethod
    def _commit(repo, msg):
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True)

    def test_the_baseline_is_intact(self, tmp_path):
        # without this, every assertion below could pass for the wrong reason
        p = self._anchored(tmp_path)
        out = verify_anchor(p)
        assert out["status"] == "intact", out["findings"]
        assert sorted(out["entries"]) == ["ledger", "registration", "test_look"]

    @pytest.mark.parametrize("victim", ["test_look", "ledger"])
    def test_deleting_an_anchored_entry_breaks_it(self, tmp_path, victim):
        p = self._anchored(tmp_path)
        side = tmp_path / "r.anchor.json"
        doc = json.loads(side.read_text())
        del doc["entries"][victim]
        side.write_text(json.dumps(doc, indent=2))
        out = verify_anchor(p)
        assert out["status"] == "broken", out
        assert any("REMOVED" in f for f in out["findings"]), out["findings"]

    def test_deleting_the_test_look_entry_also_erases_the_ordering(self,
                                                                   tmp_path):
        # the ordering IS the claim a git anchor makes; dropping the entry
        # dropped the claim and left the record reading intact
        p = self._anchored(tmp_path)
        side = tmp_path / "r.anchor.json"
        doc = json.loads(side.read_text())
        del doc["entries"]["test_look"]
        side.write_text(json.dumps(doc, indent=2))
        out = verify_anchor(p)
        assert out["ordering"] is None            # the evidence is gone
        assert out["status"] == "broken"          # and that is not "fine"
        assert report_data(p, tmp_path / "r.jsonl",
                           sims=200)["verdict"]["status"] == "CONTRADICTED"


class TestAnchorPathsStayInsideTheRepository:
    """``repo / rel`` is not a containment check.

    An absolute ``rel`` discards ``repo`` entirely and ``../`` walks out of
    it, so verification read whatever a crafted sidecar named — a file
    outside the checkout, or a special file with no end to it — before
    returning any verdict, on a record that by construction comes from
    somewhere untrusted.
    """

    def _repo(self, tmp_path):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        p = tmp_path / "r.json"
        reg.freeze(p)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "f"], cwd=tmp_path, check=True)
        return p

    def _with_path(self, tmp_path, p, rel):
        (tmp_path / "r.anchor.json").write_text(json.dumps(
            {"kind": "git", "repo": str(tmp_path),
             "entries": {"registration": {"commit": "HEAD", "path": "r.json"},
                         "ledger": {"commit": "HEAD", "path": rel}}}))
        return verify_anchor(p)

    @pytest.mark.parametrize("rel", [
        "/etc/hostname",
        "../../../../../../etc/hostname",
        "sub/../../escape.json",
        "/dev/zero",
    ])
    def test_a_path_outside_the_repository_is_refused(self, tmp_path, rel):
        p = self._repo(tmp_path)
        out = self._with_path(tmp_path, p, rel)
        assert out["status"] == "unverifiable"
        assert any("inside this repository" in f for f in out["findings"])

    def test_it_is_refused_BEFORE_the_file_is_read(self, tmp_path):
        # the point is not the verdict, it is that nothing outside the
        # checkout was opened to reach it
        p = self._repo(tmp_path)
        secret = tmp_path.parent / "secret_not_in_repo.txt"
        secret.write_text("x")
        opened = []
        real = Path.read_bytes

        def watched(self, *a, **k):
            opened.append(str(self))
            return real(self, *a, **k)

        with mock.patch.object(Path, "read_bytes", watched):
            self._with_path(tmp_path, p, str(secret))
        assert not any("secret_not_in_repo" in o for o in opened), opened

    def test_an_ordinary_nested_path_is_still_accepted(self, tmp_path):
        # the guard must not refuse the legitimate case it guards
        p = self._repo(tmp_path)
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "t.jsonl").write_text("")
        out = self._with_path(tmp_path, p, "sub/t.jsonl")
        assert out["status"] != "unverifiable", out["findings"]


class TestAnUnboundedReadCannotHappen:
    """Containment is not the only guard, because a guard can be regressed.

    A mutation test that disabled ``_inside`` drove ``read_bytes()`` on
    ``/dev/zero`` — an infinite stream — and took the whole machine down
    three times, killing the editor session with it (the processes shared a
    systemd scope with ``OOMPolicy=stop``). The path guard is the primary
    defence; this is the one that holds when the primary does not, and it
    also covers a device or FIFO sitting INSIDE the repository, where
    containment has nothing to say.
    """

    def _repo_with(self, tmp_path, entry_path):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        p = tmp_path / "r.json"
        reg.freeze(p)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "f"], cwd=tmp_path, check=True)
        (tmp_path / "r.anchor.json").write_text(json.dumps(
            {"kind": "git", "repo": str(tmp_path),
             "entries": {"registration": {"commit": "HEAD", "path": "r.json"},
                         "ledger": {"commit": "HEAD", "path": entry_path}}}))
        return p

    def test_a_character_device_inside_the_repo_is_not_read(self, tmp_path,
                                                            monkeypatch):
        """Containment passes here — the path really is inside the repo —
        so only the regular-file check can stop the read."""
        p = self._repo_with(tmp_path, "zero")
        target = tmp_path / "zero"

        # a device node needs root; simulate one by making is_file() false
        # for this path exactly, and read_bytes() the trap it would be
        target.write_text("")
        real_is_file, real_read = Path.is_file, Path.read_bytes

        def not_a_file(self):
            return False if self.name == "zero" else real_is_file(self)

        def exploding_read(self, *a, **k):
            if self.name == "zero":
                raise AssertionError("read an unbounded special file")
            return real_read(self, *a, **k)

        monkeypatch.setattr(Path, "is_file", not_a_file)
        monkeypatch.setattr(Path, "read_bytes", exploding_read)
        out = verify_anchor(p)              # must not raise
        assert out["status"] == "broken"
        assert any("not an ordinary file" in f for f in out["findings"])

    def test_a_directory_is_not_read(self, tmp_path):
        p = self._repo_with(tmp_path, "adir")
        (tmp_path / "adir").mkdir()
        out = verify_anchor(p)
        assert out["status"] == "broken"
        assert any("not an ordinary file" in f for f in out["findings"])

    def test_an_ordinary_file_is_still_read(self, tmp_path):
        # the backstop must not refuse the case it exists to let through
        p = self._repo_with(tmp_path, "r.json")
        out = verify_anchor(p)
        assert not any("not an ordinary file" in f for f in out["findings"])


class TestTheSidecarItselfIsGuarded:
    """The guards applied to paths the code DERIVED, and none to the path it
    was HANDED.

    A tracked symlink named ``*.anchor.json`` pointing at ``/dev/zero`` was
    read whole — the same unbounded read that had just been fixed one layer
    in, arriving through the front door, because the sidecar was parsed
    before the repository was even resolved.
    """

    def _repo(self, tmp_path):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        p = tmp_path / "r.json"
        reg.freeze(p)
        return p

    def test_a_sidecar_symlinked_to_an_endless_stream_is_not_read(
            self, tmp_path):
        p = self._repo(tmp_path)
        (tmp_path / "r.anchor.json").symlink_to("/dev/zero")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "f"], cwd=tmp_path, check=True)
        opened = []
        real = Path.read_text

        def watched(self, *a, **k):
            opened.append(str(self))
            return real(self, *a, **k)

        with mock.patch.object(Path, "read_text", watched):
            out = verify_anchor(p)
        assert out["status"] == "unverifiable"
        assert not any("anchor.json" in o for o in opened), opened

    def test_a_sidecar_resolving_outside_the_checkout_is_refused(self,
                                                                 tmp_path):
        p = self._repo(tmp_path)
        elsewhere = tmp_path.parent / "planted.json"
        elsewhere.write_text('{"kind": "git", "entries": {}}')
        (tmp_path / "r.anchor.json").symlink_to(elsewhere)
        out = verify_anchor(p)
        assert out["status"] == "unverifiable"
        assert any("outside the checkout" in f for f in out["findings"])

    def test_a_sidecar_that_is_a_directory_is_refused(self, tmp_path):
        p = self._repo(tmp_path)
        (tmp_path / "r.anchor.json").mkdir()
        out = verify_anchor(p)
        assert out["status"] == "unverifiable"

    def test_an_ordinary_sidecar_still_verifies(self, tmp_path):
        # the guard must not refuse the case it exists to let through
        p = self._repo(tmp_path)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "f"], cwd=tmp_path, check=True)
        nullbar.anchor(p)
        assert verify_anchor(p)["status"] in ("intact", "broken")
        assert not any("outside the checkout" in f
                       for f in verify_anchor(p)["findings"])

    def test_no_anchor_at_all_is_still_unanchored_not_unverifiable(self,
                                                                   tmp_path):
        # reordering must not turn "there is no anchor" into "we could not
        # check the anchor" — they are different answers
        p = self._repo(tmp_path)
        assert verify_anchor(p)["status"] == "unanchored"


class TestARefusalIsAVerdictNotATraceback:
    """Guards were added inside the verification loop with nothing there to
    catch them, so a record that tripped one crashed ``nullbar verify`` and
    report generation instead of reading ``broken``."""

    def _repo(self, tmp_path):
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
        reg = nullbar.Registration(
            name="r", hypothesis="h", design={},
            bar={"t": {"metric": "t", "op": ">=", "value": 3.0}},
            cells_budget=1)
        p = tmp_path / "r.json"
        reg.freeze(p)
        led = tmp_path / "r.jsonl"
        led.write_text("")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "f"], cwd=tmp_path, check=True)
        nullbar.anchor(p, ledger=led)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "a"], cwd=tmp_path, check=True)
        return p

    def test_an_oversized_working_file_is_broken_not_a_crash(self, tmp_path):
        """The cap has to sit BETWEEN the sidecar and the entry: patching it
        to 1 trips the sidecar's own read first and returns `unverifiable`,
        which is correct for that record and tests nothing about the loop.
        """
        from nullbar import _records
        p = self._repo(tmp_path)
        led = tmp_path / "r.jsonl"
        led.write_text("x" * 20_000)
        side_size = (tmp_path / "r.anchor.json").stat().st_size
        assert side_size < 10_000 < 20_000
        with mock.patch.object(_records, "MAX_RECORD_BYTES", 10_000):
            out = verify_anchor(p)          # must not raise
        assert out["status"] == "broken", out
        assert any("could not be read" in f for f in out["findings"]), out

    def test_an_oversized_COMMITTED_blob_is_refused_before_it_is_read(
            self, tmp_path):
        """A three-line sidecar can name a huge historical blob, and
        ``git cat-file blob`` allocates all of it before any guard sees a
        byte — the same unbounded read arriving from the object database
        instead of the filesystem."""
        # NOT `import nullbar.anchor as A`: the package re-exports `anchor`
        # as a FUNCTION, so that binds the function and shadows the module.
        import sys
        A = sys.modules["nullbar.anchor"]
        p = self._repo(tmp_path)
        read = []
        real = subprocess.run

        def watched(cmd, *a, **k):
            if len(cmd) > 2 and cmd[1] == "cat-file" and cmd[2] == "blob":
                read.append(list(cmd))
            return real(cmd, *a, **k)

        # A cap of 1 would refuse everything including the sidecar's own
        # blob; the point is that the OVERSIZED one specifically is never
        # fetched while ordinary ones still are.
        (tmp_path / "r.jsonl").write_text("x" * 20_000)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "big"], cwd=tmp_path,
                       check=True)
        nullbar.anchor(p, ledger=tmp_path / "r.jsonl")

        # only anchor's own name is patched, so the SIDECAR read (which uses
        # _records.MAX_RECORD_BYTES) is unaffected and the loop is reached
        with mock.patch.object(A, "MAX_RECORD_BYTES", 10_000), \
                mock.patch("subprocess.run", watched):
            out = verify_anchor(p)
        assert out["status"] == "broken", out
        assert not any("r.jsonl" in c[-1] for c in read), \
            f"fetched the oversized blob: {read}"
        assert read, "fetched nothing at all — the cap refused everything"

    def test_a_refusal_in_the_SIDECAR_SELF_CHECK_is_caught_too(self,
                                                               tmp_path):
        """The loop got its ``except`` and the self-check below it did not —
        the instance fixed, the class not — so an oversized sidecar blob
        crashed ``nullbar verify`` from three lines past the fix. Reaching
        it needs the sidecar's own committed blob over the cap while the
        entry blobs stay under, which is why it is its own test.
        """
        import sys
        A = sys.modules["nullbar.anchor"]
        p = self._repo(tmp_path)
        side = tmp_path / "r.anchor.json"
        doc = json.loads(side.read_text())
        doc["_padding"] = "x" * 20_000          # blob well over the cap
        side.write_text(json.dumps(doc))
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "pad"], cwd=tmp_path,
                       check=True)
        assert side.stat().st_size > 15_000

        # _records' cap is untouched, so the sidecar still PARSES and the
        # loop still runs; only _blob refuses, in the self-check
        with mock.patch.object(A, "MAX_RECORD_BYTES", 10_000):
            out = verify_anchor(p)              # must not raise
        assert out["status"] == "broken", out
        assert any("anchor record itself could not be read" in f
                   for f in out["findings"]), out["findings"]

    def test_an_ordinary_record_is_untouched_by_the_cap(self, tmp_path):
        p = self._repo(tmp_path)
        assert verify_anchor(p)["status"] in ("intact", "broken")
        assert not any("could not be read" in f
                       for f in verify_anchor(p)["findings"])
