"""Rendering for ``report_data`` — one self-contained HTML file.

No external assets, no scripts, no fonts fetched: the artifact has to still
render in five years, from an email attachment, on a machine with no
network. It prints to PDF from any browser (the print stylesheet keeps each
section off page breaks).

Deliberately unit-agnostic. This library never learns whether a return is a
percent or a fraction, so the report shows the numbers the research recorded
and says so, rather than appending a ``%`` it cannot justify.
"""
from __future__ import annotations

import html
import json
import math
from typing import Any

_STATUS_NOTE = {
    "PASS": "Every registered condition was met, graded against the frozen "
            "file.",
    "FAIL": "At least one registered condition was not met.",
    "INCOMPLETE": "The record does not establish a verdict. This is not a "
                  "pass.",
    "CONTRADICTED": "The frozen bar and the recorded grading disagree. No "
                    "verdict can be read off this record.",
}

_CSS = """
:root { --ink:#1a1a1a; --mute:#5f5f5f; --rule:#d8d4cd; --bg:#faf9f7;
        --pass:#1f6b3a; --fail:#8f2323; --warn:#8a5a00; }
* { box-sizing:border-box; }
body { margin:0; padding:2.2rem 1.5rem 4rem; background:var(--bg);
       color:var(--ink); font:16px/1.55 Georgia, 'Times New Roman', serif; }
.page { max-width:52rem; margin:0 auto; }
h1 { font-size:1.6rem; margin:0 0 .2rem; letter-spacing:-.01em; }
h2 { font-size:1.05rem; margin:2.4rem 0 .6rem; text-transform:uppercase;
     letter-spacing:.09em; font-family:system-ui,sans-serif;
     border-bottom:1px solid var(--rule); padding-bottom:.35rem; }
h2 .n { color:var(--mute); font-weight:400; margin-right:.5rem; }
p { margin:.5rem 0; }
.sub { color:var(--mute); font-size:.92rem; margin:0 0 1.4rem; }
.mono, code, pre, td.num { font-family:'SF Mono',Menlo,Consolas,monospace; }
table { border-collapse:collapse; width:100%; font-size:.92rem;
        font-family:system-ui,sans-serif; margin:.4rem 0 .2rem; }
th { text-align:left; font-weight:600; color:var(--mute); font-size:.78rem;
     text-transform:uppercase; letter-spacing:.06em;
     border-bottom:1px solid var(--rule); padding:.35rem .6rem .35rem 0; }
td { padding:.42rem .6rem .42rem 0; border-bottom:1px solid #ece9e3;
     vertical-align:top; }
td.num { text-align:right; white-space:nowrap; }
.banner { border:1px solid var(--rule); border-left:5px solid var(--mute);
          background:#fff; padding:1rem 1.2rem; margin:1.2rem 0 .4rem; }
.banner .verdict { font-size:1.5rem; font-family:system-ui,sans-serif;
                   font-weight:700; letter-spacing:.02em; }
.banner.PASS { border-left-color:var(--pass); } .PASS .verdict{color:var(--pass);}
.banner.FAIL { border-left-color:var(--fail); } .FAIL .verdict{color:var(--fail);}
.banner.INCOMPLETE,.banner.CONTRADICTED { border-left-color:var(--warn); }
.INCOMPLETE .verdict,.CONTRADICTED .verdict { color:var(--warn); }
.tag { font-family:system-ui,sans-serif; font-size:.72rem; font-weight:700;
       letter-spacing:.05em; padding:.12rem .45rem; border-radius:3px;
       border:1px solid currentColor; white-space:nowrap; }
.t-pass{color:var(--pass);} .t-fail{color:var(--fail);}
.t-missing,.t-invalid{color:var(--warn);}
ul { margin:.4rem 0; padding-left:1.2rem; } li { margin:.25rem 0; }
pre { background:#fff; border:1px solid var(--rule); padding:.8rem 1rem;
      overflow-x:auto; font-size:.78rem; line-height:1.4; }
.note { color:var(--mute); font-size:.85rem; }
footer { margin-top:2.6rem; border-top:1px solid var(--rule);
         padding-top:.9rem; color:var(--mute); font-size:.85rem; }
@media print {
  body { background:#fff; padding:0; font-size:11pt; }
  h2, table, pre, .banner { break-inside:avoid; }
  h2 { break-after:avoid; }
}
"""


def _is_num(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _esc_text(value: str) -> str:
    """Escape for element content only — quotes are left alone so that
    'view source' yields the frozen registration byte for byte, which is
    what re-hashing it requires."""
    return html.escape(value, quote=False)


def _fmt(value: Any, digits: int = 4) -> str:
    """A number as recorded, or an explicit dash. Never a blank cell — a
    blank reads as zero to a skimming eye."""
    if value is None:
        return "&mdash;"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "not finite"
        return f"{value:,.{digits}f}"
    return _esc(value)


def _rows(pairs: list[tuple[str, Any]], digits: int = 4) -> str:
    """Label/value rows. A value that is already a ``<td>`` passes through;
    everything else is formatted and escaped. Labels are literals from this
    module and may carry entities (``&mdash;``), so they are NOT escaped —
    never pass recorded data as a label."""
    out = []
    for label, value in pairs:
        cell = (value if isinstance(value, str) and value.startswith("<td")
                else f'<td class="num">{_fmt(value, digits)}</td>')
        out.append(f"<tr><td>{label}</td>{cell}</tr>")
    return "".join(out)


def _table(pairs: list[tuple[str, Any]], head: tuple[str, str] = ("", ""),
           digits: int = 4) -> str:
    header = (f"<tr><th>{_esc(head[0])}</th>"
              f'<th style="text-align:right">{_esc(head[1])}</th></tr>'
              if any(head) else "")
    return f"<table>{header}{_rows(pairs, digits)}</table>"


def _section(n: int, title: str, body: str) -> str:
    return f'<h2><span class="n">{n}</span>{_esc(title)}</h2>{body}'


def _missing(what: str) -> str:
    return (f'<p class="note">Not on the record: {_esc(what)}. This section '
            "is empty because nothing was recorded, not because nothing "
            "was found.</p>")


def render_html(data: dict[str, Any]) -> str:
    """The full artifact. Input is ``report.report_data`` output."""
    reg, seal = data["registration"], data["seal"]
    verdict, defl, trials = data["verdict"], data["deflation"], data["trials"]
    status = verdict["status"]
    parts: list[str] = []

    parts.append(
        f'<h1>{_esc(reg["name"])}</h1>'
        f'<p class="sub">Pre-registered evaluation report &middot; generated '
        f'{_esc(data["generated_at"])} by nullbar '
        f'{_esc(data["nullbar_version"])}</p>')

    counts = []
    if verdict["failed"]:
        counts.append(f'{len(verdict["failed"])} condition(s) not met')
    if verdict["missing"]:
        counts.append(f'{len(verdict["missing"])} not established')
    if verdict["invalid"]:
        counts.append(f'{len(verdict["invalid"])} graded by a non-boolean')
    parts.append(
        f'<div class="banner {status}"><div class="verdict">{status}</div>'
        f'<p>{_esc(_STATUS_NOTE[status])}'
        + (f' {_esc("; ".join(counts))}.' if counts else "")
        + f'</p><p class="note">Hypothesis: {_esc(reg["hypothesis"])}</p>'
          "</div>")

    if data["findings"]:
        parts.append("<h2>Findings</h2><ul>" + "".join(
            f"<li><strong>{_esc(f)}</strong></li>"
            for f in data["findings"]) + "</ul>")

    # 1 — registration and seal
    body = _table([
        ("Registered", reg["created_at"]),
        ("File", reg["path"]),
        ("sha256", f'<td class="num mono">{_esc(reg["sha256"])}</td>'),
        ("Test look spent", seal["test_look_at"] or "never"),
        ("Look bound to this file", seal["stamp_bound"]),
        ("Registered cell budget", reg["cells_budget"]),
    ])
    body += ('<p class="note">The sha256 above is of exactly the text below, '
             "with no trailing newline. Re-hash it to confirm the design and "
             "the bar are the ones that were frozen.</p>"
             f'<pre>{_esc_text(reg["frozen_text"])}</pre>')
    parts.append(_section(1, "The frozen registration", body))

    # 2 — the search
    src = defl["n_cells_source"]
    body = _table([
        ("Trials on the ledger", trials["count"]),
        ("Ledger", trials["path"] or "not supplied"),
        ("Registered cell budget", trials["budget"]),
        ("Over budget", trials["over_budget"]),
        ("Sharpe spread across trials (variance)", trials["sr_variance"]),
    ], digits=6)
    body += ('<p class="note">Deflation counts cells the way |t| does: a '
             "signal tested long and short is one two-sided cell, not two."
             + (f' Cells used below: {_esc(src)}.' if src else "") + "</p>")
    parts.append(_section(2, "The search", body))

    # 3 — null control
    null = data["null"]
    if null:
        body = _table([
            ("Machinery verdict", null.get("ok")),
            ("Composition-matched expectation", null.get("expected_gross")),
            ("Worst |t| vs that expectation",
             null.get("max_abs_t_vs_expected")),
            ("Unconditional hold, same clustering",
             (null.get("hold") or {}).get("gross")),
            ("Seeds", len(null.get("per_seed") or []) or None),
            ("Measured", null.get("measured")),
        ])
        body += ('<p class="note">A machinery check, not an edge: it asks '
                 "whether the pipeline invents an effect on scrambled "
                 "returns beyond what the assets it holds pay "
                 "unconditionally.</p>")
    else:
        body = _missing("no null control in the test-look payload")
    parts.append(_section(3, "The null control", body))

    # 4 — the clustered result
    result = data["result"]
    if result:
        body = _table([
            ("Trades", result.get("trades")),
            ("Clusters (the unit of inference)", result.get("clusters")),
            ("Gross, per trade", result.get("gross")),
            ("Cluster mean", result.get("cluster_mean")),
            ("Clustered t", result.get("t")),
        ])
        per_year = result.get("per_year") or {}
        if per_year:
            rows = "".join(
                f"<tr><td>{_esc(y)}</td>"
                f'<td class="num">{_fmt(v)}</td></tr>'
                for y, v in sorted(per_year.items(), key=lambda kv: str(kv[0])))
            body += ("<table><tr><th>Year</th>"
                     '<th style="text-align:right">Mean per block</th></tr>'
                     f"{rows}</table>")
        hold = data["hold"]
        if hold:
            body += _table([("Unconditional hold, same clustering",
                             hold.get("gross")),
                            ("Hold clusters", hold.get("clusters"))])
    else:
        body = _missing("no clustered result in the test-look payload")
    parts.append(_section(4, "The held-out result", body))

    # 5 — fills
    fills = data["fills"]
    if fills:
        rows = "".join(
            f"<tr><td>{_esc(name)}</td>"
            f'<td class="num">{_fmt((fills.get(name) or {}).get("n"))}</td>'
            f'<td class="num">{_fmt((fills.get(name) or {}).get("gross"))}</td>'
            "</tr>" for name in ("assumed", "touch", "through")
            if isinstance(fills.get(name), dict))
        body = ("<table><tr><th>Fill assumption</th>"
                '<th style="text-align:right">n</th>'
                '<th style="text-align:right">Gross</th></tr>'
                f"{rows}</table>")
        haircut = (data["metrics"] or {}).get("fill_haircut")
        if haircut is not None:
            body += _table([("Touch / assumed", haircut)])
        legs = {k: (fills.get(k) or {}).get("gross")
                for k in ("assumed", "touch")}
        if _is_num(legs["touch"]) and legs["touch"] <= 0 \
                and _is_num(legs["assumed"]) and legs["assumed"] > 0:
            body += ('<p class="note"><strong>Under resting-fill pricing '
                     "this record has no gross left to haircut: the touch "
                     "leg is negative.</strong> The ratio above crosses "
                     "zero and is not a fraction that survived.</p>")
        body += ('<p class="note">The entries that never fill are '
                 "disproportionately the good ones, so the assumed row is "
                 "an upper bound on what any of this could have executed "
                 "at.</p>")
    else:
        body = _missing("no fill bracket in the test-look payload")
    parts.append(_section(5, "Fill realism", body))

    # 6 — deflation
    clears = defl["clears"]
    body = _table([
        ("Cells searched", defl["n_cells"]),
        ("Degrees of freedom (clusters &minus; 1)", defl["df"]),
        ("Observed |t|", defl["observed_abs_t"]),
        ("Noise clears this 5% of the time (95th pct)", defl["threshold_95"]),
        ("Median of the noise maximum", defl["median"]),
        ("Observed clears the 5% line",
         clears if clears is not None else None),
        ("Deflated Sharpe probability", defl["dsr"]),
    ])
    notes = []
    if defl["n_cells_note"]:
        notes.append(_esc(defl["n_cells_note"]).capitalize() + ".")
    notes += ["Thresholds are simulated at report time from the recorded cell "
             f'and cluster counts ({defl["sims"]:,} draws, seed '
             f'{defl["seed"]}) &mdash; seeded, so they reproduce.']
    if defl["df"] is None and defl["n_cells"]:
        notes.append("No cluster count on the record, so the normal "
                     "approximation was used; it UNDERSTATES the threshold.")
    if defl["sr_source"]:
        notes.append(f'Sharpe: {defl["sr_source"]}.')
    if defl["dsr_source"]:
        notes.append(f'Deflated Sharpe: {defl["dsr_source"]}.')
    else:
        notes.append("Deflated Sharpe is unmeasured; it returns nothing "
                     "rather than 0.0, because a verdict and a shrug must "
                     "not be the same number.")
    notes.append("The MEAN of the noise maximum is not a bar &mdash; pure "
                 "noise beats its own expected maximum about 45% of the "
                 "time. The 5% line is.")
    body += '<p class="note">' + " ".join(notes) + "</p>"
    parts.append(_section(6, "Deflation", body))

    # 7 — the graded bar
    rows = "".join(
        f'<tr><td>{_esc(r["name"])}</td>'
        f'<td>{_esc(r["requirement"])}'
        + (f'<br><span class="note">{_esc(r["detail"])}</span>'
           if r["detail"] else "")
        + f'</td><td class="num">{_fmt(r["observed"])}</td>'
          f'<td class="note">{_esc(r["source"])}</td>'
          f'<td><span class="tag t-{r["state"]}">'
          f'{_esc(r["state"].upper())}</span></td></tr>'
        for r in verdict["rows"])
    body = ("<table><tr><th>Condition</th><th>As registered</th>"
            '<th style="text-align:right">Observed</th>'
            "<th>Graded</th><th></th></tr>" + rows + "</table>")
    budget = verdict["budget"]
    if budget:
        body += _table([("Cells registered", budget["registered"]),
                        ("Cells spent", budget["spent"]),
                        ("Within budget", budget["ok"])])
    body += ('<p class="note">Graded against the file on disk'
             + ("" if verdict["verified"] else " &mdash; NOT verified against "
                                               "a frozen file")
             + ". Unregistered conditions are ignored: adding one until "
               "something passes is the failure this library exists to "
               "prevent.</p>")
    parts.append(_section(7, "The bar, as graded", body))

    # 8 — everything else that was recorded
    extra = {k: v for k, v in (data["metrics"] or {}).items()
             if k not in ("trades", "clusters", "gross", "cluster_mean", "t",
                          "per_year")}
    if extra:
        body = _table([(_esc(k), v if not isinstance(v, (dict, list))
                        else json.dumps(v, default=str))
                       for k, v in sorted(extra.items())], digits=6)
        body += ('<p class="note">Every remaining top-level entry of the '
                 "test-look payload, so nothing recorded is hidden by this "
                 "rendering.</p>")
        parts.append(_section(8, "The rest of the record", body))

    # 9 — what is missing
    if data["gaps"]:
        parts.append(_section(9, "What this record does not contain",
                              "<ul>" + "".join(f"<li>{_esc(g)}</li>"
                                               for g in data["gaps"])
                              + "</ul>"))

    parts.append(
        "<footer><p><strong>What this report is.</strong> A rendering of a "
        "pre-registration, its trial ledger and its single held-out test "
        "look, as they exist on disk. It reports what was promised and what "
        "was measured. It is not investment advice, not an offer, and not a "
        "track record; nothing here forecasts any future result.</p>"
        "<p><strong>What it does not prove.</strong> The record is "
        "tamper-<em>evident</em>, not tamper-<em>proof</em>: it binds the "
        "test look to the registration by hash, so an edit after the fact is "
        "visible &mdash; but anyone with write access can delete both and "
        "start over. For a third party to rely on it, the registration and "
        "the stamp must be anchored somewhere the researcher does not "
        "control (a commit, an append-only store, a timestamping "
        "authority).</p>"
        "<p>Figures are in the units the research recorded; this library is "
        "unit-agnostic and does not convert them.</p></footer>")

    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>'
            f'{_esc(reg["name"])} &mdash; nullbar report</title>'
            f"<style>{_CSS}</style></head><body>"
            f'<div class="page">{"".join(parts)}</div></body></html>')
