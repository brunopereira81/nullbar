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

_VERBS = ("report", "anchor", "verify", "lint")


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


def _anchor(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="nullbar anchor",
        description="Record which commits carry a registration and its "
                    "test-look stamp, so the order they were written in "
                    "stops depending on the researcher's clock.")
    ap.add_argument("registration")
    ap.add_argument("--commit", action="store_true",
                    help="commit these two paths first (never your staged "
                         "work)")
    ap.add_argument("-m", "--message", help="commit message for --commit")
    args = ap.parse_args(argv)

    from .anchor import GitError, anchor
    try:
        doc = anchor(args.registration, commit=args.commit,
                     message=args.message)
    except FileNotFoundError as exc:
        print(f"nullbar anchor: {exc}", file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"nullbar anchor: {exc}", file=sys.stderr)
        return 2
    for role, entry in doc["entries"].items():
        print(f"{role}: {entry['commit'][:12]} {entry['path']}")
    print(f"anchor written to "
          f"{Path(args.registration).with_suffix('.anchor.json')}")
    return 0


def _verify(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="nullbar verify",
        description="Check a recorded anchor against the repository now.")
    ap.add_argument("registration")
    args = ap.parse_args(argv)

    from .anchor import verify_anchor
    result = verify_anchor(args.registration)
    print(f"anchor: {result['status']}"
          + ("" if result["witnessed"] else "  (local only)"))
    for finding in result["findings"]:
        print(f"  ! {finding}", file=sys.stderr)
    for note in result["notes"]:
        print(f"  - {note}")
    # anything but an intact anchor exits non-zero: "not anchored" and
    # "anchor holds" must not be the same answer to a CI job.
    return 0 if result["status"] == "intact" else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-V", "--version"):
        print(f"nullbar {__version__}")
        return 0
    if argv and argv[0] == "report":
        return _report(argv[1:])
    if argv and argv[0] == "anchor":
        return _anchor(argv[1:])
    if argv and argv[0] == "verify":
        return _verify(argv[1:])
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
