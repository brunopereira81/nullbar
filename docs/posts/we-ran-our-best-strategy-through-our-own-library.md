# We ran our best strategy through our own library. It failed. Here's the printout.

*2026-08-12 · Second post. The [first one](the-leak-that-survived-two-years.md)
was about the leak that ate two years. This one is about the strategy that
survived the leak — our best, most-replicated, most-loved result — and what
happened when we fed it to [nullbar](https://github.com/brunopereira81/nullbar),
the library we extracted from the wreckage.*

## The strategy

The one real thing two years of research produced: hourly crypto mean
reversion. Buy when an asset's distance below its own 168-hour moving average
crosses its own expanding 10th percentile; hold exactly 24 hours; exit. No ML.
Four lines of logic. It beat our transformer ensemble ~4× net, its magnitude
reproduced across five independent samples to within 0.05 percentage points,
and at one point a naive pooled test read **t = 3.79**. We very much wanted it
to be real.

Here is the entire evaluation, run live against seven years of data, as seven
printed lines:

```
[1] registered: sha256 28fef10f90555de8…
[2] trials on record: 64  (null expects max |t| ~ 2.60)
[3] null control: max |t| across seeds = 2.77  (OK)
[4] OOS: 1815 trades, 560 clusters, gross +0.235%/trade,
    cluster mean +0.597%, clustered t +2.42
    2022 (the bear year): -0.616%
[5] fill bracket: assumed +0.015% -> touch -0.035% -> through -0.064%
    (net at 0.230% RT: -0.265% to -0.294%)
[6] deflated Sharpe probability (n_trials=64): 0.518
[7] VERDICT: FAIL   failed: ['gross_2x_cost', 't3', 'year_2022_positive']
    second test look refused
```

## Reading the printout

**Line 1** is a promise with a tamper seal: the rule, the real trading cost,
and four pass conditions, hashed *before* evaluation. One condition is
"positive in 2022" — because a dip-buyer that only works in bull markets is
leveraged beta wearing a lab coat.

**Line 2** is the confession that changes everything. We didn't test one
strategy; we tested sixteen signals at two horizons in two directions — 64
cells. The parenthesis is the sentence most retail quants never hear: **after
64 tries, pure luck's best result is typically t ≈ 2.6.** Whatever wins your
search must be judged against *that*, not against zero.

**Line 3**: scrambled returns through the identical pipeline find nothing.
The machine doesn't manufacture effects. Run this before believing anything —
if your pipeline finds edge in noise, every other number it prints is fiction.

**Line 4** is our beloved strategy meeting line 2. Clustered properly (one
entry per asset per 24h block — that alone deflated the naive 3.79 to the
2.4–2.7 range), the effect is **t = 2.42 against a luck-of-64 baseline of
2.60**. Our best discovery is statistically indistinguishable from being the
winner of our own search. And 2022 says **−0.616%**: in the year that
separates edge from bull beta, it lost.

**Line 5** prices reality. A resting limit bid doesn't always fill — and the
misses aren't random. When we measured 416k labeled bars: bids filled 92–96%
of the time, but the entries where price never came back averaged +1.6%. The
best trades are precisely the ones you don't get. Bracket the fills between
"touched" and "traded through", subtract the real 0.230% round trip, and
every trade nets negative.

**Line 6** compresses the whole story into one number: after the 64-trial
penalty, the probability that this reflects skill rather than selection is
**0.518. A coin flip.** For calibration: the library's demo plants a *real*
edge in synthetic data and confesses only 4 trials — it scores 0.555. Trial
count is that powerful, which is why the ledger it comes from is append-only
with no delete API.

**Line 7** is why we built the thing. Not "it doesn't work" — three *named
broken promises*: doesn't clear 2× its costs, doesn't clear the luck
threshold, died in the bear year. A verdict you can act on. And then the last
line, the one that saves accounts: the second test look **raises an
exception**. In August we enforced that with discipline. Now it's code.

## What this cost, and what it saved

The effect is probably not even fake — its magnitude reproduced too well
across independent samples for pure mirage. It is simply *smaller than the
costs of harvesting it and the statistics of proving it*, which for a trading
account is the same thing as not existing. Finding that out via this printout
cost some compute. Finding it out the other way — deploying on t=2.4-worth of
hope at real size — is how most stories in this genre end, and those stories
never get written up because their authors are busy telling themselves it was
variance.

Every backtest is a claim. Most are false in ways their authors cannot see —
we were careful, tested, reviewed, and wrong for two years. The seven lines
above are what "checked" looks like. The library that prints them is MIT,
[on GitHub](https://github.com/brunopereira81/nullbar), and the walkthrough
script is `examples/`-simple. Run your favorite strategy through it. One of
two printouts comes out, and either one is worth more than what you paid.
