"""The examples are documentation — CI proves they run and say what the
docs claim they say."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
EX = ROOT / "examples"


def _run(name: str) -> str:
    env = dict(os.environ)
    # examples must run from a bare checkout too, not only when installed
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, str(EX / name)],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_full_workflow_example():
    out = _run("01_full_workflow.py")
    assert "VERDICT: PASS" in out          # the planted effect is real
    assert "second test look refused" in out


def test_catch_a_leak_example():
    out = _run("02_catch_a_leak.py")
    assert "causal_feature   -> clean" in out
    assert "zscore_leak      -> LEAK" in out
    assert "mtf_leak         -> LEAK" in out
