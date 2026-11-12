"""Estimator correctness, checked against the closed forms rather than against
previously-recorded outputs.

A statistics module that is only tested against its own past behaviour will
happily keep a sign error forever. These compare to the combinatorial
definitions, and the coverage test checks the intervals actually cover.
"""

from __future__ import annotations

from math import comb

import numpy as np
import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from autobench.scoring.aggregate import (
    Interval,
    TaskOutcome,
    aggregate_run,
    bootstrap_interval,
    compare_runs,
    gate_regression,
    mcnemar_exact,
    pass_at_k,
    pass_caret_k,
    wilson_interval,
)

# ---------------------------------------------------------------------------
# pass@k
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "c", "k"),
    [(10, 3, 1), (10, 3, 5), (20, 7, 3), (5, 0, 2), (5, 5, 3), (8, 2, 8), (100, 37, 10)],
)
def test_pass_at_k_matches_closed_form(n: int, c: int, k: int) -> None:
    expected = 1.0 - (comb(n - c, k) / comb(n, k) if n - c >= k else 0.0)
    assert pass_at_k(n, c, k) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize(
    ("n", "c", "k"),
    [(10, 3, 1), (10, 3, 3), (20, 7, 3), (5, 5, 3), (6, 2, 3), (50, 25, 5)],
)
def test_pass_caret_k_matches_closed_form(n: int, c: int, k: int) -> None:
    expected = comb(c, k) / comb(n, k) if c >= k else 0.0
    assert pass_caret_k(n, c, k) == pytest.approx(expected, abs=1e-12)


def test_pass_at_k_edges() -> None:
    assert pass_at_k(5, 5, 1) == 1.0
    assert pass_at_k(5, 0, 1) == 0.0
    assert pass_at_k(0, 0, 1) == 0.0
    with pytest.raises(ValueError, match="at least"):
        pass_at_k(3, 1, 5)


def test_pass_caret_k_is_never_above_pass_at_k() -> None:
    """The reliability number can never exceed the capability number."""
    for n in range(1, 25):
        for c in range(n + 1):
            for k in range(1, n + 1):
                assert pass_caret_k(n, c, k) <= pass_at_k(n, c, k) + 1e-12


@given(
    n=st.integers(min_value=1, max_value=60),
    frac=st.floats(min_value=0.0, max_value=1.0),
    k=st.integers(min_value=1, max_value=10),
)
@hyp_settings(max_examples=200, deadline=None)
def test_estimators_stay_in_unit_interval(n: int, frac: float, k: int) -> None:
    c = round(frac * n)
    k = min(k, n)
    assert 0.0 <= pass_at_k(n, c, k) <= 1.0
    assert 0.0 <= pass_caret_k(n, c, k) <= 1.0


# ---------------------------------------------------------------------------
# intervals
# ---------------------------------------------------------------------------


def test_wilson_is_not_degenerate_at_zero() -> None:
    """Wald would report [0, 0] here, claiming certainty from 20 observations."""
    low, high = wilson_interval(0, 20)
    assert low == 0.0
    assert 0.1 < high < 0.25


def test_wilson_is_not_degenerate_at_one() -> None:
    low, high = wilson_interval(20, 20)
    assert high == 1.0
    assert 0.75 < low < 0.9


def test_wilson_narrows_with_n() -> None:
    wide = wilson_interval(5, 10)
    narrow = wilson_interval(500, 1000)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_covers_truth_about_95_percent_of_the_time() -> None:
    """Coverage simulation — the property the interval actually claims."""
    rng = np.random.default_rng(1337)
    truth = 0.3
    trials = 40
    covered = 0
    repeats = 4_000
    for _ in range(repeats):
        successes = int(rng.binomial(trials, truth))
        low, high = wilson_interval(successes, trials)
        if low <= truth <= high:
            covered += 1
    coverage = covered / repeats
    # Wilson is slightly conservative by construction; anything under 0.93 is a bug.
    assert 0.93 <= coverage <= 0.995, f"coverage was {coverage:.3f}"


def test_bootstrap_is_deterministic_for_a_seed() -> None:
    values = [0.1, 0.5, 0.9, 0.3, 0.7, 0.2]
    first = bootstrap_interval(values, resamples=500, seed=7)
    second = bootstrap_interval(values, resamples=500, seed=7)
    assert first == second


def test_bootstrap_degenerate_input() -> None:
    result = bootstrap_interval([0.5] * 10, resamples=100)
    assert result.low == result.high == 0.5
    assert "degenerate" in result.method


def test_interval_overlap() -> None:
    a = Interval(0.5, 0.4, 0.6, "wilson")
    b = Interval(0.55, 0.45, 0.65, "wilson")
    c = Interval(0.9, 0.85, 0.95, "wilson")
    assert a.overlaps(b)
    assert not a.overlaps(c)


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def test_reliability_gap_is_reported() -> None:
    """A model that solves each task sometimes has a large gap; that is the point."""
    tasks = [TaskOutcome(f"t{i}", attempts=5, passes=3) for i in range(20)]
    aggregate = aggregate_run(tasks, k=5, resamples=500)
    assert aggregate.pass_at_k.point > aggregate.pass_caret_k.point
    assert aggregate.reliability_gap > 0.5


def test_perfect_run_has_no_gap() -> None:
    tasks = [TaskOutcome(f"t{i}", attempts=5, passes=5) for i in range(10)]
    aggregate = aggregate_run(tasks, k=5, resamples=500)
    assert aggregate.pass_at_1.point == 1.0
    assert aggregate.reliability_gap == pytest.approx(0.0, abs=1e-9)


def test_k_below_three_is_not_reportable() -> None:
    tasks = [TaskOutcome("t0", attempts=1, passes=1)]
    assert not aggregate_run(tasks, k=1, resamples=100).reportable
    tasks = [TaskOutcome("t0", attempts=3, passes=2)]
    assert aggregate_run(tasks, k=3, resamples=100).reportable


def test_empty_run_does_not_claim_zero() -> None:
    aggregate = aggregate_run([], k=3)
    assert aggregate.total_trials == 0
    # An interval of [0, 1] says "we know nothing", which is the truth.
    assert aggregate.pass_at_1.low == 0.0
    assert aggregate.pass_at_1.high == 1.0


# ---------------------------------------------------------------------------
# comparison and gating
# ---------------------------------------------------------------------------


def test_known_flip_is_described_correctly() -> None:
    baseline = [TaskOutcome(f"t{i}", 5, 5 if i < 30 else 0) for i in range(50)]
    candidate = [TaskOutcome(f"t{i}", 5, 5 if i < 35 else 0) for i in range(50)]
    comparison = compare_runs(baseline, candidate, resamples=1_000)

    assert comparison.shared_tasks == 50
    assert len(comparison.flips) == 5
    assert len(comparison.improved) == 5
    assert len(comparison.regressed) == 0
    assert "5 tasks flipped" in comparison.headline()


def test_mcnemar_exact_values() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    # 10 improvements, 0 regressions: 2 * (1/2)^10
    assert mcnemar_exact(10, 0) == pytest.approx(2 * 0.5**10, abs=1e-12)
    assert mcnemar_exact(5, 5) == 1.0


def test_gate_fails_on_a_real_regression() -> None:
    baseline = [TaskOutcome(f"t{i}", 5, 5 if i < 40 else 0) for i in range(50)]
    candidate = [TaskOutcome(f"t{i}", 5, 5 if i < 30 else 0) for i in range(50)]
    verdict = gate_regression(compare_runs(baseline, candidate, resamples=1_000))
    assert not verdict.passed
    assert verdict.exit_code == 1
    assert len(verdict.regressed_tasks) == 10


def test_gate_passes_within_tolerance() -> None:
    baseline = [TaskOutcome(f"t{i}", 5, 5 if i < 40 else 0) for i in range(50)]
    candidate = [TaskOutcome(f"t{i}", 5, 5 if i < 39 else 0) for i in range(50)]
    verdict = gate_regression(compare_runs(baseline, candidate, resamples=1_000))
    assert verdict.passed
    assert verdict.exit_code == 0


def test_gate_does_not_block_on_an_inconclusive_wobble() -> None:
    """Blocking a merge on a coin flip trains people to ignore the gate.

    Two tasks regress and one improves on a 25-task suite: the point estimate
    moves down, but the interval on the difference straddles zero, so the
    honest answer is "not enough trials to tell".
    """
    baseline = [TaskOutcome(f"t{i}", 3, 3 if i < 20 else 0) for i in range(25)]
    candidate = [TaskOutcome(f"t{i}", 3, 3 if (i < 18 or i == 20) else 0) for i in range(25)]
    comparison = compare_runs(baseline, candidate, resamples=2_000)
    assert comparison.inconclusive
    assert comparison.candidate_score < comparison.baseline_score

    lenient = gate_regression(comparison, require_significance=True)
    assert lenient.passed
    assert "straddles zero" in lenient.reason

    strict = gate_regression(comparison, require_significance=False)
    assert not strict.passed


def test_comparison_of_disjoint_task_sets_is_inconclusive() -> None:
    comparison = compare_runs([TaskOutcome("a", 3, 3)], [TaskOutcome("b", 3, 0)], resamples=100)
    assert comparison.shared_tasks == 0
    assert comparison.inconclusive
