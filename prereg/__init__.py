"""prereg — pre-registration and honest statistics for trading research.

Born from two years of production algo trading whose most valuable outputs
were true negatives: a feature leak that explained a deployed model's entire
measured edge, a fee assumption that overstated every result 1.6x, a fill
assumption worth another 1.3-1.5x, and a string of t≈2.7 effects that never
survived a pre-registered bar. This library is the machinery that caught
them, extracted.

The workflow: register the design and the bar BEFORE running (registration),
count every trial (ledger), deflate accordingly (stats), cluster your
inference (evaluate), run the null control first (evaluate), price your
fills honestly (fills), and let the prefix-replay check hunt your leaks
(leaklint). The held-out test is one look.
"""
from .ledger import TrialLedger
from .registration import AlreadySpentError, Registration
from .stats import (clustered_t, dsr, expected_max_abs_t,
                    expected_max_sharpe, psr, sharpe)
from .evaluate import block_cluster_eval, null_control, shuffle_within_columns
from .fills import fill_bracket, through_mask, touch_mask
from .leaklint import LintHit, lint_source, prefix_replay_check

__version__ = "0.1.0"
__all__ = [
    "TrialLedger", "Registration", "AlreadySpentError",
    "clustered_t", "sharpe", "psr", "dsr", "expected_max_sharpe",
    "expected_max_abs_t",
    "block_cluster_eval", "null_control", "shuffle_within_columns",
    "touch_mask", "through_mask", "fill_bracket",
    "lint_source", "prefix_replay_check", "LintHit",
]
