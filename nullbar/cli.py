"""``nullbar`` — the command line: ``report`` and ``lint``.

Bare paths still lint (``python3 -m nullbar strategy/``), which is what the
first release documented and what the ``nullbar-lint`` entry point does; the
verb form is additive.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__

_VERBS = ("report", "lint")


def _report(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="nullbar report",
        description="Render a frozen registration, its ledger and its "
                    "test-look stamp as one self-contained HTML report.")
    ap.add_argument("registration", help="path to the frozen registration "
                                         "JSON")
    ap.add_argument("-o", "--out", help="output path (default: the "
                                        "registration path with .html)")
    ap.add_argument("--ledger", help="path to the trial ledger JSONL; "
                                     "without it the trial count is not on "
                                     "the record")
    ap.add_argument("--json", action="store_true",
                    help="write the report's facts as JSON instead of HTML")
    ap.add_argument("--sims", type=int, default=100_000,
                    help="simulation draws for the deflation thresholds")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    from .report import report_data
    from .report_html import render_html

    try:
        data = report_data(args.registration, args.ledger, sims=args.sims,
                           seed=args.seed)
    except FileNotFoundError as exc:
        print(f"nullbar report: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"nullbar report: cannot read the record: {exc}",
              file=sys.stderr)
        return 2

    suffix = ".json" if args.json else ".html"
    out = Path(args.out) if args.out else \
        Path(args.registration).with_suffix(suffix)
    payload = (json.dumps(data, indent=2, default=str) if args.json
               else render_html(data))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload)

    verdict = data["verdict"]
    print(f"{data['registration']['name']}: {verdict['status']} -> {out}")
    for finding in data["findings"]:
        print(f"  ! {finding}", file=sys.stderr)
    # A report that could not establish a verdict exits non-zero: in CI, an
    # incomplete record must not be indistinguishable from a passing one.
    return 0 if verdict["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-V", "--version"):
        print(f"nullbar {__version__}")
        return 0
    if argv and argv[0] == "report":
        return _report(argv[1:])
    from .leaklint import main as lint_main
    if argv and argv[0] == "lint":
        return lint_main(argv[1:])
    if not argv:
        print(f"usage: nullbar {{{','.join(_VERBS)}}} ... "
              f"(paths alone lint)\nnullbar {__version__}", file=sys.stderr)
        return 2
    return lint_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
