# The leak that survived two years, 2,000 tests, and every code review

*2026-08-12 · The first post from the wreckage of a production trading
system. It ends with a 50-millisecond check that would have saved us a year.*

## The setup

For two years I ran a Python algorithmic trading system on crypto — live
capital, TimescaleDB, exchange reconciliation, a test suite that grew past
2,000 tests. The strategy layer was an ensemble transformer predicting
short-horizon returns from hourly bars. It backtested beautifully. Its
walk-forward, out-of-sample edge measured **+1.8% net per trade** at a
24-hour horizon, positive in five out of five regime windows. We sized the
live deployment on that number.

The number was a leak. Not a subtle one, in hindsight — a 23-hour crime in
one line of feature code that every review had blessed.

## The leak

The feature pipeline enriched each hourly bar with higher-timeframe
context: 4-hour and daily aggregates — trend, volatility, distance from
moving averages at the coarser scale. Standard practice. The code mapped
each 1h bar to the 4h/daily bucket it *sits in*:

```python
daily = close.resample("1D").last()
features["daily_trend"] = daily.reindex(hourly_index, method="ffill")
```

Look harmless? The daily bucket that the 01:00 bar "sits in" **closes at
23:00 that night**. Its value is decided 22 hours in the future. The 13:00
bar's 4h bucket closes at 15:59. Every bar was fed a summary of a future it
could not have seen — up to +23 hours of it, against a 6-hour prediction
label.

Three properties made it nearly invisible:

1. **It looks like bookkeeping, not prediction.** Resampling and reindexing
   read as data plumbing. No reviewer asks whether a *join* sees the future.
2. **It poisoned training AND backtesting identically**, so backtest-live
   parity checks — which we enforced with a dedicated test layer — passed.
   Both sides of the parity were drinking from the same well.
3. **Live inference couldn't reproduce it** — live's final bucket truncates
   at "now" — so the model was served a slightly different feature
   distribution in production than in training. It underperformed its
   backtest persistently, and we attributed the gap to fees and fills,
   because fees and fills were real problems too.

## The measurement

When we finally suspected the alignment (a code-review sweep flagged the
bucket mapping), we didn't patch it quietly. We ran the A/B: the same model
evaluated on leaky versus corrected feature alignment, engine-free, on five
regime windows that predated every training set, scored as top-vs-bottom
decile spread of 24h forward returns.

- Leaky alignment: **+7.30%** median 24h spread, rank correlation **0.406**,
  positive in 5/5 regimes.
- Corrected alignment: **+0.18%**, rank correlation **0.019**, 3/5 regimes.

The edge wasn't degraded by the fix. It *was* the leak. Retraining with 3×
patience produced bit-identical weights — the model had learned the future,
because the future was the only learnable thing in the data. Every
downstream decision built on that +1.8%/trade — the live deployment, the
sizing, months of strategy iteration — had been built on it.

## The check that would have caught it on day one

The general form of this bug class: **a feature whose past values change
when the future is appended.** That property is mechanically testable,
without understanding the feature at all:

```python
def prefix_replay_check(feature_fn, data, cuts=(0.5, 0.75, 0.9)):
    full = feature_fn(data)
    for frac in cuts:
        k = int(len(data) * frac)
        prefix = feature_fn(data.iloc[:k])
        if not values_match(prefix, full.iloc[:k]):
            return "LEAK"
    return "clean"
```

Compute the feature on a prefix. Compare with the full-sample computation
over the same rows. Any mismatch means some past value depends on the
future. It catches this leak, full-sample normalization, backfills,
shuffled splits — the whole family — in about 50ms per feature.

One wrinkle we found while packaging it: if a cut lands exactly on a bucket
boundary (midnight, for a daily leak), the prefix's final bucket is complete
and the leak is invisible at that cut. So the check must probe two cuts at a
prime offset apart. The bug class defends itself.

## What we do differently now

The fix wasn't just the check. It was admitting that we — careful,
test-obsessed, statistically literate — fooled ourselves for two years, and
that the only version of this discipline that works is the one that runs as
*code*, before results exist:

- **Pre-register** the design and the pass bar; freeze it in a hash.
- **Count every trial** in an append-only ledger, because a best-of-64
  search has a null max |t| of ~2.7 and human memory reports "just this one
  idea".
- **Null-control first**: shuffled returns through the identical pipeline
  must come back flat.
- **Cluster inference** on time blocks (overlapping windows inflated
  another of our results by 1.9×).
- **Bracket fills**: our resting bids filled 92–96% of the time, but the
  missed 4–8% were the best trades — executed gross was ~0.7× assumed.
- **One test look.** The code raises if you take a second one.

We packaged all of it as **[prereg](https://github.com/brunopereira81/prereg)**
(MIT, the statistics stay open). The README tells the rest of the story,
including the part where we used this harness to design, register, train,
and honestly kill a brand-new model in a single day — a question that used
to cost us months per wrong answer.

The system that taught us all this ended up flat: every edge we could
measure was smaller than the costs to harvest it. That's not the outcome we
wanted, but it's the outcome that was *true*, and we got it at the price of
compute instead of capital. If your backtest is beautiful, I hope it's
real. Here's the 50ms that tells you.
