"""Tests for git anchoring.

Every test drives a REAL repository through subprocess. Mocking git here
would test a model of git, and the whole claim of this module is about what
git actually does to history when someone rewrites it.
"""
from __future__ import annotations

import json
import subprocess

import pytest

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
        assert report_data(path, sims=2000)["verdict"]["status"] == "PASS"
        doc = json.loads(path.read_text())
        doc["bar"]["t3"]["value"] = 1.0
        path.write_text(json.dumps(doc, indent=2, sort_keys=True))
        data = report_data(path, sims=2000)
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
