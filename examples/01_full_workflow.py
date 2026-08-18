"""The full honest workflow, end to end, on synthetic data.

Run:  python3 examples/01_full_workflow.py

Story: we simulate 8 assets of hourly returns, plant a small real effect on
a signal, then walk the exact sequence the library enforces:

    register -> count trials (with their Sharpes) -> null control vs hold
    -> clustered eval -> fill bracket -> deflate -> ONE test look -> verdict

Everything prints; nothing here needs market data or an exchange.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import nullbar

rng = np.random.default_rng(7)
workdir = Path(tempfile.mkdtemp(prefix="nullbar_demo_"))

# ── synthetic market: 8 assets, 6000 hourly bars, tiny planted edge ─────────
n, k = 6000, 8
idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
cols = [f"ASSET{i}" for i in range(k)]
fwd = pd.DataFrame(rng.normal(0.0, 1.0, (n, k)), index=idx, columns=cols)
signal = pd.DataFrame(rng.random((n, k)) < 0.08, index=idx, columns=cols)
fwd_real = fwd + signal * 0.35          # signal bars pay +0.35% on average

# split: first ~5 months are "research", the rest is the TEST
cut = pd.Timestamp("2024-06-01", tz="UTC")
research = idx < cut

# ── 1. register BEFORE running ──────────────────────────────────────────────
reg = nullbar.Registration(
    name="demo-signal",
    hypothesis="the demo signal predicts next-period returns",
    design={"entry_rate": 0.08, "hold": "1 bar", "cost_pct": 0.10},
    bar={
        "null_flat": "null control indistinguishable from holding",
        "t3": "clustered t >= 3.0 on the TEST window",
        "net_positive": "gross - cost > 0 on the TEST window",
    },
)
reg_path = workdir / "demo-signal.json"
print(f"registered: sha256 {reg.freeze(reg_path)[:16]}…  -> {reg_path}")

# ── 2. every variant you try goes in the ledger, WITH its Sharpe ────────────
# the ledger is where deflation gets both of its numbers: how many cells you
# searched, and how much their Sharpes varied. Record the metric and you
# never have to invent the spread.
ledger = nullbar.TrialLedger(workdir / "trials.jsonl")
for rate in (0.05, 0.08, 0.12, 0.15):
    variant = pd.DataFrame(rng.random((n, k)) < rate, index=idx, columns=cols)
    if rate == 0.08:
        variant = signal                    # the one we registered
    r = nullbar.block_cluster_eval(variant[research], fwd_real[research],
                                   block="24h")
    ledger.record("demo-signal", {"entry_rate": rate},
                  metrics={"sr": r["t"] / np.sqrt(r["clusters"])})
print(f"trials on record: {ledger.count()}  "
      f"(sr spread across them: {ledger.sr_variance():.5f})")

# ── 3. null control FIRST — machinery check, before any real number ─────────
nv = nullbar.null_verdict(signal[research], fwd_real[research],
                          block="24h", seeds=(0, 1, 2))
print(f"null control: scrambled runs reproduce their holdings' "
      f"unconditional {nv['expected_gross']:+.3f}% and nothing more "
      f"(max |t| {nv['max_abs_t_vs_expected']:.2f}) "
      f"({'OK' if nv['ok'] else 'PIPELINE BROKEN — stop here'}); "
      f"holding pays {nv['hold']['gross']:+.3f}%")

# ── 4. the research-window number (free to look at) ─────────────────────────
res = nullbar.block_cluster_eval(signal[research], fwd_real[research],
                                 block="24h")
print(f"research window: {res['trades']} trades, {res['clusters']} clusters, "
      f"gross {res['gross']:+.3f}%, clustered t {res['t']:+.2f}")

# ── 5. price fills honestly (toy limit/low series) ──────────────────────────
close = 100 + pd.DataFrame(rng.normal(0, 0.4, (n, k)), index=idx,
                           columns=cols).cumsum()
low = close - rng.uniform(0.0, 0.8, (n, k))
bracket = nullbar.fill_bracket(signal[research], close[research],
                               low[research], fwd_real[research])
print("fill bracket:", {kk: f"{v['gross']:+.3f}% (n={v['n']})"
                        for kk, v in bracket.items()})

# ── 6. deflate by what you actually searched ────────────────────────────────
# the observed Sharpe must be in the SAME units as the recorded ones —
# here per 24h block, which is t / sqrt(clusters) for both.
observed_sr = res["t"] / np.sqrt(res["clusters"])
d = nullbar.dsr(observed_sr=observed_sr, n=res["clusters"],
                n_trials=ledger.count(),
                sr_variance=ledger.sr_variance())
luck = nullbar.expected_max_abs_t(ledger.count(), df=res["clusters"] - 1)
print(f"deflated Sharpe probability (n_trials={ledger.count()}): {d:.3f}   "
      f"| luck-of-{ledger.count()} threshold: |t| ~ {luck:.2f}")

# ── 7. ONE test look, then the verdict against the frozen bar ───────────────
test = nullbar.block_cluster_eval(signal[~research], fwd_real[~research],
                                  block="24h")
reg.spend_test_look(reg_path, results=test)
# note: no bool() wrappers — numpy comparisons are graded correctly, and
# anything that is not unambiguously true fails.
verdict = reg.verdict({
    "null_flat": nv["ok"],
    "t3": test["t"] >= 3.0,
    "net_positive": test["gross"] - 0.10 > 0,
})
print(f"TEST: gross {test['gross']:+.3f}%, t {test['t']:+.2f}")
print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'} "
      f"(failed: {verdict['failed'] or 'none'}, "
      f"graded against the frozen file: {verdict['verified']})")

# and the enforcement:
try:
    reg.spend_test_look(reg_path, results={"t": 99})
except nullbar.AlreadySpentError as e:
    print(f"second test look refused: {e}")
