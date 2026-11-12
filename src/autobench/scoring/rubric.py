"""LLM-as-judge, deliberately constrained.

Autobench does not judge prose by default. Where a task genuinely has no
deterministic oracle — explanation adequacy, prose quality — a judge is
available under four constraints, each of which exists because of a specific
way judges go wrong:

1. **Never when ``task.deterministic = true``.** Enforced in the schema, not here.
2. **The judge model is pinned and recorded.** A judge change invalidates
   comparability with every prior run, and the UI flags it rather than
   pretending the numbers still line up.
3. **The judge never sees the reference solution.** Shown one, a judge stops
   grading the rubric and starts measuring similarity to the reference, which
   scores paraphrase highly and correct-but-different answers badly.
4. **Calibration is mandatory.** At least 20 human-labelled examples, with
   Cohen's kappa reported alongside the run. Below 0.6 the suite fails
   validation, because a judge that disagrees with humans is not measuring
   the thing the rubric describes.

Deterministic checks run first and do the work they can; the judge is the
expensive last layer, added only where it reduces real risk.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from autobench.models.router import ModelRef, ModelRouter
from autobench.models.types import ChatMessage, ChatRequest
from autobench.scoring.report import ScoreItem, ScoreReport

logger = logging.getLogger(__name__)

MIN_CALIBRATION_EXAMPLES = 20
MIN_KAPPA = 0.6

JUDGE_SYSTEM = """\
You are grading one piece of work against a rubric. You will be shown the rubric
and the work. You will NOT be shown a reference solution, because your job is to
apply the rubric, not to measure similarity to someone else's answer.

For each rubric criterion, decide pass or fail and give a one-sentence reason
citing the specific evidence you used.

Respond with JSON only:
{"items": [{"id": "<criterion id>", "passed": true, "detail": "<reason>"}]}
"""


@dataclass(slots=True)
class RubricCriterion:
    id: str
    description: str
    weight: float = 1.0


@dataclass(slots=True)
class CalibrationExample:
    """One human-labelled example: the ground truth the judge is measured against."""

    output: str
    human_labels: dict[str, bool]


@dataclass(slots=True)
class JudgeResult:
    report: ScoreReport
    judge_model: str
    raw_response: str = ""
    kappa: float | None = None


@dataclass(slots=True)
class RubricScorer:
    criteria: list[RubricCriterion]
    judge_model: str
    router: ModelRouter
    calibration: list[CalibrationExample] = field(default_factory=list)

    async def score(self, work: str, *, seed: int = 1337) -> JudgeResult:
        ref = ModelRef.parse(self.judge_model)
        rubric_text = "\n".join(
            f"- [{c.id}] {c.description} (weight {c.weight})" for c in self.criteria
        )
        prompt = (
            f"# Rubric\n{rubric_text}\n\n"
            f"# Work to grade\n```\n{work[:40_000]}\n```\n\n"
            "Return the JSON object described in your instructions."
        )
        request = ChatRequest(
            model=ref.model_id,
            messages=[ChatMessage.user(prompt)],
            system=JUDGE_SYSTEM,
            temperature=0.0,
            max_tokens=2048,
            seed=seed,
        )
        response, _ = await self.router.chat(ref, request)
        items = self._parse(response.text)
        report = ScoreReport.from_items(items, pass_threshold=1.0, weighted=True)
        return JudgeResult(report=report, judge_model=self.judge_model, raw_response=response.text)

    def _parse(self, text: str) -> list[ScoreItem]:
        weights = {c.id: c.weight for c in self.criteria}
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.warning("judge returned unparseable output; scoring every criterion failed")
            return [
                ScoreItem(id=c.id, passed=False, weight=c.weight, detail="judge output unparseable")
                for c in self.criteria
            ]

        graded = {str(item.get("id")): item for item in data.get("items", [])}
        out: list[ScoreItem] = []
        for criterion in self.criteria:
            item: dict[str, Any] = graded.get(criterion.id, {})
            out.append(
                ScoreItem(
                    id=criterion.id,
                    passed=bool(item.get("passed", False)),
                    weight=weights[criterion.id],
                    detail=str(item.get("detail") or "not graded by the judge"),
                )
            )
        return out

    async def calibrate(self, *, seed: int = 1337) -> float:
        """Judge the calibration set and report Cohen's kappa against the humans."""
        if len(self.calibration) < MIN_CALIBRATION_EXAMPLES:
            raise ValueError(
                f"rubric scoring needs at least {MIN_CALIBRATION_EXAMPLES} human-labelled "
                f"calibration examples; got {len(self.calibration)}"
            )
        judge_labels: list[bool] = []
        human_labels: list[bool] = []
        for example in self.calibration:
            result = await self.score(example.output, seed=seed)
            for item in result.report.items:
                if item.id in example.human_labels:
                    judge_labels.append(item.passed)
                    human_labels.append(example.human_labels[item.id])
        return cohens_kappa(judge_labels, human_labels)


def cohens_kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    """Agreement corrected for chance.

    Raw agreement is misleading on skewed label distributions: two raters who
    both say "pass" 95% of the time agree 90% of the time by accident. Kappa
    subtracts that floor.
    """
    if not a or len(a) != len(b):
        return 0.0
    n = len(a)
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n

    a_true = sum(a) / n
    b_true = sum(b) / n
    expected = a_true * b_true + (1 - a_true) * (1 - b_true)

    if expected >= 1.0:
        # Both raters were unanimous and identical: perfect agreement, but
        # kappa is undefined (0/0). Reporting 1.0 is the honest reading.
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def kappa_verdict(kappa: float) -> tuple[bool, str]:
    if kappa >= 0.8:
        return True, f"kappa={kappa:.2f}: strong judge-human agreement"
    if kappa >= MIN_KAPPA:
        return True, f"kappa={kappa:.2f}: acceptable judge-human agreement"
    return False, (
        f"kappa={kappa:.2f} is below the {MIN_KAPPA} floor. The judge does not agree "
        "with human labels often enough for its scores to mean anything; fix the "
        "rubric or change the judge model."
    )
