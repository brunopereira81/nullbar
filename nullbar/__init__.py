"""nullbar — pre-registration and honest statistics for trading research.

Born from two years of production algo trading whose most valuable outputs
were true negatives: a feature leak that explained a deployed model's entire
measured edge, a fee assumption that overstated every result 1.6x, a fill
assumption worth another 1.3-1.5x, and a string of t≈2.7 effects that never
survived a pre-registered bar. This library is the machinery that caught
them, extracted.

The workflow: register the design and the bar BEFORE running (registration),
count every trial (ledger), deflate accordingly (stats), cluster your
inference (evaluate), run the null control against the hold baseline
(evaluate), price your fills honestly (fills), and let the prefix-replay
check hunt your leaks (leaklint). The held-out test is one look.

Named for the two things it makes you commit to before you are allowed to
believe a number: the NULL control and the pre-registered BAR.
"""
__version__ = "0.7.1"

from ._records import RecordReadError
from .ledger import TrialLedger, UnlockablePlatformError
from .registration import (AlreadySpentError, AmbiguousConditionError,
                           AtomicPublishUnsupportedError,
                           BarMismatchError, Registration, SealBrokenError,
                           spec_text)
from .stats import (clustered_t, dsr, expected_max_abs_t,
                    expected_max_sharpe, psr, sharpe)
from .evaluate import (block_cluster_eval, hold_baseline, null_control,
                       null_verdict, shuffle_within_columns)
from .fills import fill_bracket, through_mask, touch_mask
from .leaklint import (LeakError, LintHit, assert_no_leak, lint_source,
                       prefix_replay_check)
from .anchor import GitError, anchor, verify_anchor
from .report import evidence, report_data
from .report_html import render_html

__all__ = [
    # A caller that wants to CATCH a refusal needs to be able to name it.
    # Both of these are new in 0.7.1 and both are named in the changelog;
    # neither was importable from the package, so `except
    # nullbar.RecordReadError` raised AttributeError and the only way in
    # was a private module.
    "RecordReadError", "UnlockablePlatformError",
    "AtomicPublishUnsupportedError",
    "TrialLedger", "Registration", "AlreadySpentError", "SealBrokenError",
    "AmbiguousConditionError", "BarMismatchError", "spec_text",
    "clustered_t", "sharpe", "psr", "dsr", "expected_max_sharpe",
    "expected_max_abs_t",
    "block_cluster_eval", "hold_baseline", "null_control", "null_verdict",
    "shuffle_within_columns",
    "touch_mask", "through_mask", "fill_bracket",
    "lint_source", "prefix_replay_check", "assert_no_leak", "LintHit",
    "LeakError",
    "evidence", "report_data", "render_html",
    "anchor", "verify_anchor", "GitError",
]
