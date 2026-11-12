# Make the retry decorator deterministic

`retry.py` implements a `@retry` decorator with exponential backoff. It is
flaky under load: the backoff is computed from wall-clock time, so two runs
of the same code retry a different number of times depending on how busy the
machine is.

Fix it so that:

1. The number of attempts for a given failure sequence is **deterministic** —
   it must not depend on how long anything took.
2. The existing behaviour in `test_retry_existing.py` keeps working.
3. The hot path does not block. Wall-clock sleeping inside `retry.py` is
   what makes the suite slow and non-reproducible; the delay must be
   injectable instead.

The public signature of `retry` must not change.
