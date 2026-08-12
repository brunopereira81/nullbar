"""The full honest workflow, end to end, on synthetic data.

Run:  python examples/01_full_workflow.py

Story: we simulate 8 assets of hourly returns, plant a small real effect on
a signal, then walk the exact sequence the library enforces:

    register -> count trials -> null control -> clustered eval
    -> fill bracket -> deflate -> spend the ONE test look -> verdict

Everything prints; nothing here needs market data or an exchange.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import prereg

rng = np.random.default_rng(7)
workdir = Path(tempfile.mkdtemp(prefix="prereg_demo_"))

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
reg = prereg.Registration(
    name="demo-signal",
    hypothesis="the demo signal predicts next-period returns",
    design={"entry_rate": 0.08, "hold": "1 bar", "cost_pct": 0.10},
    bar={
        "null_flat": "null-control |t| < 3 on research window",
        "t3": "clustered t >= 3.0 on the TEST window",
        "net_positive": "gross - cost > 0 on the TEST window",
    },
)
reg_path = workdir / "demo-signal.json"
print(f"registered: sha256 {reg.freeze(reg_path)[:16]}…  -> {reg_path}")

# ── 2. every variant you try goes in the ledger ─────────────────────────────
ledger = prereg.TrialLedger(workdir / "trials.jsonl")
ledger.record("demo-signal", {"entry_rate": 0.08})
# (imagine the 63 other variants you tried last month…)
for th in (0.05, 0.10, 0.15):
    ledger.record("demo-signal-rejected", {"entry_rate": th})
print(f"trials on record: {ledger.count()}")

# ── 3. null control FIRST ───────────────────────────────────────────────────
nulls = prereg.null_control(signal[research], fwd_real[research],
                            block="24h", seeds=(0, 1, 2))
worst = max(abs(x["t"]) for x in nulls)
print(f"null control: max |t| across seeds = {worst:.2f}  "
      f"({'OK' if worst < 3 else 'PIPELINE BROKEN — stop here'})")

# ── 4. the research-window number (free to look at) ─────────────────────────
res = prereg.block_cluster_eval(signal[research], fwd_real[research],
                                block="24h")
print(f"research window: {res['trades']} trades, {res['clusters']} clusters, "
      f"gross {res['gross']:+.3f}%, clustered t {res['t']:+.2f}")

# ── 5. price fills honestly (toy limit/low series) ──────────────────────────
close = 100 + pd.DataFrame(rng.normal(0, 0.4, (n, k)), index=idx,
                           columns=cols).cumsum()
low = close - rng.uniform(0.0, 0.8, (n, k))
bracket = prereg.fill_bracket(signal[research], close[research],
                              low[research], fwd_real[research])
print("fill bracket:", {kk: f"{v['gross']:+.3f}% (n={v['n']})"
                        for kk, v in bracket.items()})

# ── 6. deflate by what you searched ─────────────────────────────────────────
per_trial_srs = rng.normal(0.0, 0.02, ledger.count())   # your sweep's SRs
d = prereg.dsr(observed_sr=0.03, n=res["clusters"],
               n_trials=ledger.count(),
               sr_variance=float(np.var(per_trial_srs)))
print(f"deflated Sharpe probability (n_trials={ledger.count()}): {d:.3f}")

# ── 7. ONE test look, then the verdict against the frozen bar ───────────────
test = prereg.block_cluster_eval(signal[~research], fwd_real[~research],
                                 block="24h")
reg.spend_test_look(reg_path, results=test)
verdict = reg.verdict({
    "null_flat": worst < 3,
    "t3": bool(test["t"] >= 3.0),
    "net_positive": bool(test["gross"] - 0.10 > 0),
})
print(f"TEST: gross {test['gross']:+.3f}%, t {test['t']:+.2f}")
print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'} "
      f"(failed: {verdict['failed'] or 'none'})")

# and the enforcement:
try:
    reg.spend_test_look(reg_path, results={"t": 99})
except prereg.AlreadySpentError as e:
    print(f"second test look refused: {e}")
