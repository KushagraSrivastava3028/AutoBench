"""pass@k, pass^k, and intervals that admit the whole thing is stochastic.

The product opinion, in one paragraph:

    pass@k tells you the model *can*. pass^k tells you it *will*. A UI that
    shows only the first is telling you the best case and calling it the
    result. Autobench renders both, adjacent, with the difference labelled
    **reliability gap** — because the published gap is routinely 26-29 points,
    and on some pools pass@3 of 0.99 sits next to pass^3 of 0.54.

And the statistical opinion:

    pass@k has no closed-form variance and is high-variance on small task pools,
    so a bare point estimate is not a measurement. Binomial quantities get a
    Wilson interval (correct at small n and near 0 or 1, where Wald is actively
    wrong). pass@k for k>1 gets a BCa bootstrap over tasks. Run-vs-run
    comparison gets a paired bootstrap plus McNemar on the flipped tasks —
    "17 tasks flipped: 11 to pass, 6 to fail; net +5, p=0.21", not "score went up".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

DEFAULT_CONFIDENCE = 0.95
DEFAULT_RESAMPLES = 10_000


# ---------------------------------------------------------------------------
# point estimators
# ---------------------------------------------------------------------------


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k: P(at least one of k sampled attempts passes).

    ``n`` attempts, ``c`` correct, budget ``k``. The naive form,
    ``1 - C(n-c, k) / C(n, k)``, overflows and loses precision for realistic n;
    this is the numerically stable product form used throughout the literature.
    """
    if k <= 0 or n <= 0:
        return 0.0
    if k > n:
        raise ValueError(f"pass@{k} needs at least {k} attempts, got {n}")
    if n - c < k:
        return 1.0
    # prod over i in [n-c+1, n] of (1 - k/i)
    return float(1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1)))


def pass_caret_k(n: int, c: int, k: int) -> float:
    """Unbiased pass^k: P(**all** k sampled attempts pass).

    The reliability number. The unbiased estimator is the hypergeometric
    probability of drawing k successes from c successes in n attempts:
    ``C(c, k) / C(n, k)``, computed in log space.
    """
    if k <= 0 or n <= 0:
        return 0.0
    if k > n:
        raise ValueError(f"pass^{k} needs at least {k} attempts, got {n}")
    if c < k:
        return 0.0
    if c == n:
        return 1.0
    log_ratio = (
        math.lgamma(c + 1) - math.lgamma(c - k + 1) - math.lgamma(n + 1) + math.lgamma(n - k + 1)
    )
    return float(math.exp(log_ratio))


def wilson_interval(
    successes: int, trials: int, confidence: float = DEFAULT_CONFIDENCE
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Chosen over Wald because Wald is not merely imprecise near 0 and 1 — it is
    wrong. At 0/20 successes Wald reports [0.0, 0.0], claiming certainty from
    twenty observations. Wilson reports roughly [0.0, 0.16], which is the truth.
    """
    if trials <= 0:
        return (0.0, 1.0)
    z = _z_score(confidence)
    phat = successes / trials
    denominator = 1 + z**2 / trials
    centre = (phat + z**2 / (2 * trials)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / trials + z**2 / (4 * trials**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _z_score(confidence: float) -> float:
    """Two-sided normal quantile. SciPy when present, a good rational otherwise."""
    try:
        from scipy.stats import norm

        return float(norm.ppf(1 - (1 - confidence) / 2))
    except ImportError:  # pragma: no cover
        common = {0.90: 1.6448536269, 0.95: 1.9599639845, 0.99: 2.5758293035}
        return common.get(round(confidence, 4), 1.9599639845)


# ---------------------------------------------------------------------------
# per-task and per-run aggregates
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TaskOutcome:
    """One task's attempts within one run."""

    task_id: str
    attempts: int
    passes: int
    rewards: list[float] = field(default_factory=list)
    infra_errors: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passes / self.attempts if self.attempts else 0.0

    @property
    def mean_reward(self) -> float:
        return float(np.mean(self.rewards)) if self.rewards else 0.0

    def pass_at(self, k: int) -> float:
        return pass_at_k(self.attempts, self.passes, min(k, self.attempts))

    def pass_caret(self, k: int) -> float:
        return pass_caret_k(self.attempts, self.passes, min(k, self.attempts))


@dataclass(slots=True)
class Interval:
    point: float
    low: float
    high: float
    method: str

    @property
    def margin(self) -> float:
        """Half-width. What the UI renders as ``±6.1``."""
        return (self.high - self.low) / 2

    def overlaps(self, other: Interval) -> bool:
        return not (self.high < other.low or other.high < self.low)

    def to_json(self) -> dict[str, Any]:
        return {
            "point": round(self.point, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
            "margin": round(self.margin, 4),
            "method": self.method,
        }


@dataclass(slots=True)
class RunAggregate:
    k: int
    tasks: list[TaskOutcome]
    pass_at_1: Interval
    pass_at_k: Interval
    pass_caret_k: Interval
    mean_reward: float
    total_trials: int
    total_passes: int
    infra_errors: int
    reportable: bool

    @property
    def reliability_gap(self) -> float:
        """pass@k − pass^k, in points. The number that matters for shipping."""
        return self.pass_at_k.point - self.pass_caret_k.point

    def to_json(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "pass_at_1": self.pass_at_1.to_json(),
            "pass_at_k": self.pass_at_k.to_json(),
            "pass_caret_k": self.pass_caret_k.to_json(),
            "reliability_gap": round(self.reliability_gap, 4),
            "mean_reward": round(self.mean_reward, 4),
            "total_trials": self.total_trials,
            "total_passes": self.total_passes,
            "infra_errors": self.infra_errors,
            "tasks_evaluated": len(self.tasks),
            "reportable": self.reportable,
        }


def aggregate_run(
    tasks: Sequence[TaskOutcome],
    k: int,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 1337,
    reportable_min_k: int = 3,
) -> RunAggregate:
    """Aggregate a run. Never returns a bare point estimate."""
    tasks = list(tasks)
    total_trials = sum(t.attempts for t in tasks)
    total_passes = sum(t.passes for t in tasks)

    if not tasks or total_trials == 0:
        empty = Interval(0.0, 0.0, 1.0, "wilson")
        return RunAggregate(
            k,
            tasks,
            empty,
            empty,
            empty,
            0.0,
            0,
            0,
            sum(t.infra_errors for t in tasks),
            reportable=False,
        )

    # Attempts within a task are correlated. Resample task clusters rather
    # than pretending every repeated attempt is an independent observation.
    if k == 1:
        low, high = wilson_interval(total_passes, total_trials, confidence)
        pass1 = Interval(total_passes / total_trials, low, high, "wilson")
    else:
        pass1 = bootstrap_interval(
            [t.pass_rate for t in tasks if t.attempts],
            confidence=confidence,
            resamples=resamples,
            seed=seed + 2,
        )

    # pass@k and pass^k are per-task quantities averaged over tasks: no closed
    # form for the variance, so bootstrap over the task pool.
    eligible = [t for t in tasks if t.attempts >= k]
    at_k_values = [t.pass_at(k) for t in eligible]
    caret_values = [t.pass_caret(k) for t in eligible]
    at_k = bootstrap_interval(at_k_values, confidence=confidence, resamples=resamples, seed=seed)
    caret = bootstrap_interval(
        caret_values, confidence=confidence, resamples=resamples, seed=seed + 1
    )

    rewards = [r for t in tasks for r in t.rewards]
    return RunAggregate(
        k=k,
        tasks=tasks,
        pass_at_1=pass1,
        pass_at_k=at_k,
        pass_caret_k=caret,
        mean_reward=float(np.mean(rewards)) if rewards else 0.0,
        total_trials=total_trials,
        total_passes=total_passes,
        infra_errors=sum(t.infra_errors for t in tasks),
        reportable=k >= reportable_min_k and len(eligible) == len(tasks),
    )


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


def bootstrap_interval(
    values: Sequence[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 1337,
    bca: bool = True,
) -> Interval:
    """Bootstrap the mean over tasks, BCa-corrected by default.

    BCa (bias-corrected and accelerated) matters here because per-task pass@k is
    bounded at 1.0 and piles up against that ceiling on easy suites. The
    percentile interval is visibly skewed in that regime; BCa corrects for it.
    Falls back to the percentile interval when the acceleration is undefined
    (every task identical), which is the degenerate case where they agree anyway.
    """
    array = np.asarray(list(values), dtype=float)
    n = array.size
    if n == 0:
        return Interval(0.0, 0.0, 1.0, "bootstrap")
    point = float(array.mean())
    if n == 1:
        return Interval(point, 0.0, 1.0, "bootstrap-n1")
    if float(array.std()) == 0.0:
        return Interval(point, point, point, "bootstrap-degenerate")

    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n, size=(resamples, n))
    means = array[indices].mean(axis=1)

    alpha = (1 - confidence) / 2
    if not bca:
        low, high = np.quantile(means, [alpha, 1 - alpha])
        return Interval(point, float(low), float(high), "bootstrap-percentile")

    # bias correction: where the observed statistic falls in the bootstrap dist
    proportion_below = float((means < point).mean())
    if proportion_below <= 0.0 or proportion_below >= 1.0:
        low, high = np.quantile(means, [alpha, 1 - alpha])
        return Interval(point, float(low), float(high), "bootstrap-percentile")
    z0 = _norm_ppf(proportion_below)

    # acceleration: jackknife skewness of the statistic
    total = array.sum()
    jackknife = (total - array) / (n - 1)
    deviations = jackknife.mean() - jackknife
    denominator = 6.0 * float((deviations**2).sum()) ** 1.5
    accel = float((deviations**3).sum()) / denominator if denominator else 0.0

    z_alpha_low = _norm_ppf(alpha)
    z_alpha_high = _norm_ppf(1 - alpha)
    adjusted_low = _norm_cdf(z0 + (z0 + z_alpha_low) / (1 - accel * (z0 + z_alpha_low)))
    adjusted_high = _norm_cdf(z0 + (z0 + z_alpha_high) / (1 - accel * (z0 + z_alpha_high)))
    low, high = np.quantile(means, [adjusted_low, adjusted_high])
    return Interval(point, float(low), float(high), "bootstrap-bca")


def _norm_ppf(p: float) -> float:
    try:
        from scipy.stats import norm

        return float(norm.ppf(p))
    except ImportError:  # pragma: no cover
        return float(math.sqrt(2) * _erfinv(2 * p - 1))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _erfinv(y: float) -> float:  # pragma: no cover - scipy is a hard dependency
    """Winitzki's approximation, accurate to ~1e-3. Fallback only."""
    a = 0.147
    ln = math.log(1 - y * y) if abs(y) < 1 else -30.0
    term = 2 / (math.pi * a) + ln / 2
    return math.copysign(math.sqrt(math.sqrt(term**2 - ln / a) - term), y)


# ---------------------------------------------------------------------------
# run-vs-run comparison
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TaskFlip:
    task_id: str
    before: float
    after: float

    @property
    def direction(self) -> str:
        return "improved" if self.after > self.before else "regressed"


@dataclass(slots=True)
class Comparison:
    """Two runs over the same task pool."""

    shared_tasks: int
    baseline_score: float
    candidate_score: float
    delta: Interval
    flips: list[TaskFlip]
    mcnemar_p: float
    #: True when the intervals overlap — the UI must render this grey, not
    #: green or red. The frontend is not allowed to imply a difference the
    #: statistics do not support.
    inconclusive: bool

    @property
    def improved(self) -> list[TaskFlip]:
        return [f for f in self.flips if f.direction == "improved"]

    @property
    def regressed(self) -> list[TaskFlip]:
        return [f for f in self.flips if f.direction == "regressed"]

    def headline(self) -> str:
        net = len(self.improved) - len(self.regressed)
        # ASCII deliberately: this string is rendered in terminals whose code
        # page is not always UTF-8. The web UI draws its own arrows.
        return (
            f"{len(self.flips)} tasks flipped: {len(self.improved)} -> pass, "
            f"{len(self.regressed)} -> fail; net {net:+d}, p={self.mcnemar_p:.2f}"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "shared_tasks": self.shared_tasks,
            "baseline_score": round(self.baseline_score, 4),
            "candidate_score": round(self.candidate_score, 4),
            "delta": self.delta.to_json(),
            "mcnemar_p": round(self.mcnemar_p, 4),
            "inconclusive": self.inconclusive,
            "headline": self.headline(),
            "flips": [
                {
                    "task_id": f.task_id,
                    "before": f.before,
                    "after": f.after,
                    "direction": f.direction,
                }
                for f in sorted(self.flips, key=lambda f: f.task_id)
            ],
        }


def compare_runs(
    baseline: Iterable[TaskOutcome],
    candidate: Iterable[TaskOutcome],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 1337,
) -> Comparison:
    """Paired bootstrap over the shared task set, plus McNemar on the flips.

    Paired, not independent: the same tasks appear in both runs, and treating
    them as independent samples throws away the pairing and inflates the
    interval. The paired form asks the right question — "did *these* tasks get
    better" — rather than "are these two populations different".
    """
    before = {t.task_id: t for t in baseline}
    after = {t.task_id: t for t in candidate}
    shared = sorted(set(before) & set(after))

    if not shared:
        zero = Interval(0.0, 0.0, 0.0, "paired-bootstrap")
        return Comparison(0, 0.0, 0.0, zero, [], 1.0, inconclusive=True)

    before_rates = np.array([before[t].pass_rate for t in shared])
    after_rates = np.array([after[t].pass_rate for t in shared])
    differences = after_rates - before_rates

    delta = bootstrap_interval(
        differences.tolist(), confidence=confidence, resamples=resamples, seed=seed
    )

    flips = [
        TaskFlip(task_id, float(before_rates[i]), float(after_rates[i]))
        for i, task_id in enumerate(shared)
        if not math.isclose(before_rates[i], after_rates[i], abs_tol=1e-9)
    ]

    improved = sum(1 for f in flips if f.direction == "improved")
    regressed = sum(1 for f in flips if f.direction == "regressed")
    p_value = mcnemar_exact(improved, regressed)

    # Inconclusive when the delta interval straddles zero.
    return Comparison(
        shared_tasks=len(shared),
        baseline_score=float(before_rates.mean()),
        candidate_score=float(after_rates.mean()),
        delta=delta,
        flips=flips,
        mcnemar_p=p_value,
        inconclusive=delta.low <= 0.0 <= delta.high,
    )


def mcnemar_exact(improved: int, regressed: int) -> float:
    """Exact two-sided McNemar (binomial) test on the discordant pairs.

    The exact form rather than the chi-square approximation because flip counts
    in an eval are routinely single digits, which is exactly where the
    approximation stops being trustworthy.
    """
    n = improved + regressed
    if n == 0:
        return 1.0
    smaller = min(improved, regressed)
    # Two-sided: 2 * P(X <= smaller) under Binomial(n, 0.5), capped at 1.
    tail = sum(math.comb(n, i) for i in range(smaller + 1)) / (2**n)
    return float(min(1.0, 2 * tail))


# ---------------------------------------------------------------------------
# regression gating
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GateVerdict:
    passed: bool
    reason: str
    baseline_score: float
    candidate_score: float
    delta: float
    regressed_tasks: list[str]

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def gate_regression(
    comparison: Comparison,
    *,
    max_regression: float = 0.03,
    require_significance: bool = True,
) -> GateVerdict:
    """``autobench gate`` — exit non-zero when a suite drops beyond the threshold.

    ``require_significance`` is on by default so CI does not page anyone over a
    two-task wobble on a 50-task suite. When the delta interval straddles zero,
    the honest answer is "we cannot tell from this many trials", and blocking a
    merge on a coin flip trains people to ignore the gate.
    """
    delta = comparison.candidate_score - comparison.baseline_score
    regressed = sorted(f.task_id for f in comparison.regressed)

    if delta >= -max_regression:
        return GateVerdict(
            True,
            f"score moved {delta:+.1%}, within the {max_regression:.1%} tolerance",
            comparison.baseline_score,
            comparison.candidate_score,
            delta,
            regressed,
        )

    if require_significance and comparison.inconclusive:
        return GateVerdict(
            True,
            (
                f"score moved {delta:+.1%}, but the 95% interval "
                f"[{comparison.delta.low:+.1%}, {comparison.delta.high:+.1%}] straddles zero "
                f"— not enough trials to call it. Raise k to decide."
            ),
            comparison.baseline_score,
            comparison.candidate_score,
            delta,
            regressed,
        )

    return GateVerdict(
        False,
        (
            f"regression of {abs(delta):.1%} exceeds the {max_regression:.1%} threshold; "
            f"{len(regressed)} task(s) regressed (p={comparison.mcnemar_p:.3f})"
        ),
        comparison.baseline_score,
        comparison.candidate_score,
        delta,
        regressed,
    )
