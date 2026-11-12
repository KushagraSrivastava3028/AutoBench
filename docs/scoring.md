# Scoring and statistics

Everything here lives in `src/autobench/scoring/aggregate.py`. It is tested against the closed-form definitions, not against snapshots of earlier output.

## Metrics

| Metric | Definition | Answers |
|---|---|---|
| pass@1 | mean `binary_pass` over all scored trials | the default headline |
| pass@k | P(at least one of k passes), unbiased estimator | *can* it? |
| **pass^k** | P(all k pass) = C(c,k)/C(n,k) | *will* it, every time? |
| reliability gap | pass@k − pass^k | how far to trust it |
| mean reward | mean partial credit | a diagnostic; never the headline |

pass@k and pass^k always appear together. Either one alone is misleading.

## Intervals

- **pass@1** uses a Wilson 95% interval, which stays correct near 0 and 1. At 0/20, Wald gives [0, 0]; Wilson gives [0, 0.16].
- **pass@k and pass^k** use a BCa bootstrap over tasks, 10,000 resamples, seeded by the run seed so the result is reproducible.
- A **comparison between runs** uses a paired bootstrap on per-task differences plus an exact McNemar test on the tasks that flipped.

The coverage test in `tests/unit/test_aggregate.py` simulates 4,000 binomial draws and checks that the Wilson interval contains the true value between 93% and 99.5% of the time.

## Excluded from the denominator

Trials with `infra_*` or `verifier_crash` errors never produce a `score` row, so they cannot count against the model. They appear as a separate counter in the UI.

## Reportability

A run with **k < 3** is labelled *indicative, not reportable* in the CLI, the run header and the leaderboard. One attempt per task is a single sample of a random process.

## Comparisons and the gate

```bash
autobench gate --baseline main-nightly --candidate pr-412 --max-regression 0.03
```

- If the score drops by no more than `max-regression`, the gate passes.
- If it drops by more, **but the 95% interval on the difference crosses zero**, the gate still passes (unless you pass `--strict`) and says why. Failing a merge on noise teaches people to ignore the gate.
- If it drops by more and the interval does not cross zero, the gate exits 1 and lists the tasks that regressed.

The UI enforces the same rule. When two runs' intervals overlap, the comparison is shown in grey, not green or red.

## Rubric scoring (opt-in)

`scoring/rubric.py`. Not allowed when `task.deterministic = true`. The judge model is pinned. The judge never sees a reference solution. It needs at least 20 human-labelled calibration examples, and the suite fails validation if Cohen's κ is below 0.6.
