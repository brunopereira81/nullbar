"""Axis guard shared by every function that takes more than one frame.

Two frames on different axes is not a rare mistake — it is a column
reordering, a reindex, a join that dropped a symbol. NumPy indexing across
them produces another asset's numbers with no error at all, in whichever
direction the misalignment happens to point. Every multi-frame entry point
in this package calls this first.
"""
from __future__ import annotations

import pandas as pd


def require_same_axes(**frames: pd.DataFrame) -> None:
    """Raise unless every frame shares an identical index AND columns."""
    items = list(frames.items())
    ref_name, ref = items[0]
    if not isinstance(ref, pd.DataFrame):
        raise TypeError(f"{ref_name} must be a DataFrame, got "
                        f"{type(ref).__name__}")
    for name, f in items[1:]:
        if not isinstance(f, pd.DataFrame):
            raise TypeError(f"{name} must be a DataFrame, got "
                            f"{type(f).__name__}")
        if not f.index.equals(ref.index):
            raise ValueError(
                f"{name} and {ref_name} must share an exact index "
                f"({len(f.index)} vs {len(ref.index)} rows; equal="
                f"{f.index.equals(ref.index)})")
        if not f.columns.equals(ref.columns):
            raise ValueError(
                f"{name} and {ref_name} must share exact columns in the "
                f"same order: {list(f.columns)[:6]} vs "
                f"{list(ref.columns)[:6]}")
