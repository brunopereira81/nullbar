# Contributing

Bug reports and patches are welcome. Two things are unusual about this
project and worth knowing before you open a PR.

**1. Errors in the flattering direction are the ones that matter.** A bug
that makes a strategy look worse than it is costs an afternoon. A bug that
makes one look better costs an account — v0.1.0 shipped two of those. If you
find one, that is the highest-value issue you can file, and it will be
treated as such.

**2. Tests are written against known answers, not against the code.** The
suite asserts things like `psr(0, n) == 0.5`, `expected_max_abs_t(1) ≈
√(2/π)`, and that a clustered t is invariant to duplicating observations
inside a cluster. A test that restates the implementation passes whatever
the implementation does, including when it is wrong. Please add tests of the
first kind — and where a fix is subtle, check that mutating the fix actually
fails your test.

## Working on it

```bash
git clone https://github.com/brunopereira81/nullbar && cd nullbar
pip3 install -e .[dev]
pytest -q                       # 90+ tests, ~4 s
python3 examples/01_full_workflow.py
python3 -m nullbar nullbar/
```

The examples are documentation and CI asserts their output, so if you change
what they print, update `tests/test_examples.py` in the same commit.

## Scope

In scope: measurement honesty — leak detection, deflation, clustering, fill
realism, pre-registration mechanics.

Out of scope: anything that helps find an edge (signals, optimizers,
backtest engines, data feeds). There are many good libraries for that, and
this one exists precisely because they all assume the answer is yes.
