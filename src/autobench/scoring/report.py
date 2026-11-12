"""The score report (PRD §5.4).

``run-tests.sh`` writes JSON to ``scoring.report_path``. The verifier's exit
code is a **fallback only** — a test runner that dies after printing its results
should not silently turn a pass into a fail, and a test runner that exits 0
without writing a report has not graded anything.

``binary_pass`` is what pass@k is computed on. Mean reward is reported beside
it, never instead of it: partial credit answers a different question ("how far
did it get") from the one a release decision asks ("did it work").
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPORT_SCHEMA_VERSION = "1.0"


class ScoreItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    passed: bool
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    detail: str | None = None


class ScoreReport(BaseModel):
    """What the verifier container produced, normalised."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = REPORT_SCHEMA_VERSION
    items: list[ScoreItem] = Field(default_factory=list)
    reward: float = Field(default=0.0, ge=0.0, le=1.0)
    binary_pass: bool = False
    verifier_version: str | None = None
    duration_sec: float | None = None
    #: Present when the report could not be read; see from_failure().
    error: str | None = None

    @model_validator(mode="after")
    def _clamp(self) -> ScoreReport:
        object.__setattr__(self, "reward", max(0.0, min(1.0, float(self.reward))))
        return self

    # --- construction ------------------------------------------------------

    @classmethod
    def from_items(
        cls,
        items: list[ScoreItem],
        *,
        pass_threshold: float = 1.0,
        weighted: bool = True,
        **extra: Any,
    ) -> ScoreReport:
        """Compute reward from items rather than trusting a self-reported number."""
        if not items:
            return cls(items=[], reward=0.0, binary_pass=False, **extra)
        if weighted:
            total_weight = sum(item.weight for item in items)
            if total_weight <= 0:
                reward = 0.0
            else:
                reward = sum(item.weight for item in items if item.passed) / total_weight
        else:
            reward = sum(1.0 for item in items if item.passed) / len(items)
        reward = round(reward, 3)
        return cls(
            items=items,
            reward=reward,
            binary_pass=reward >= pass_threshold - 1e-9,
            **extra,
        )

    @classmethod
    def from_failure(cls, detail: str, **extra: Any) -> ScoreReport:
        """A trial that never produced a report scores zero, and says why."""
        return cls(items=[], reward=0.0, binary_pass=False, error=detail, **extra)

    @classmethod
    def parse(
        cls,
        raw: str | bytes | dict[str, Any],
        *,
        pass_threshold: float = 1.0,
        exit_code: int | None = None,
    ) -> ScoreReport:
        """Parse a verifier's JSON report, recomputing anything it got wrong.

        Two things are deliberately not trusted:

        * A self-reported ``reward`` that disagrees with the items. The items are
          the evidence; the scalar is a summary, and a summary that disagrees
          with its evidence is a bug in the test harness.
        * A self-reported ``binary_pass``. It is derived from ``reward`` and the
          task's threshold, here, in one place.
        """
        if isinstance(raw, (str, bytes)):
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            text = text.strip()
            if not text:
                return cls.from_failure("verifier wrote an empty report")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                return cls.from_failure(f"verifier report is not valid JSON: {exc}")
        else:
            data = raw

        if not isinstance(data, dict):
            return cls.from_failure("verifier report is not a JSON object")

        items = [ScoreItem.model_validate(item) for item in data.get("items", [])]

        if items:
            report = cls.from_items(items, pass_threshold=pass_threshold)
            declared = data.get("reward")
            if declared is not None and abs(float(declared) - report.reward) > 1e-6:
                report.error = (
                    f"verifier declared reward={float(declared):.3f} but its items sum to "
                    f"{report.reward:.3f}; the items win"
                )
        else:
            # No items: fall back to the declared reward, then to the exit code.
            if "reward" in data:
                reward = round(max(0.0, min(1.0, float(data["reward"]))), 3)
            elif exit_code is not None:
                reward = 1.0 if exit_code == 0 else 0.0
            else:
                return cls.from_failure("verifier report has neither items nor a reward")
            report = cls(reward=reward, binary_pass=reward >= pass_threshold - 1e-9)

        report.schema_version = str(data.get("schema_version", REPORT_SCHEMA_VERSION))
        report.verifier_version = data.get("verifier_version")
        if "duration_sec" in data:
            try:
                report.duration_sec = float(data["duration_sec"])
            except (TypeError, ValueError):
                report.duration_sec = None
        return report

    # --- views -------------------------------------------------------------

    @property
    def failed_items(self) -> list[ScoreItem]:
        return [item for item in self.items if not item.passed]

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def summary(self) -> str:
        passed = sum(1 for item in self.items if item.passed)
        verdict = "PASS" if self.binary_pass else "FAIL"
        if self.items:
            return f"{verdict} reward={self.reward:.3f} ({passed}/{len(self.items)} items)"
        return f"{verdict} reward={self.reward:.3f}"
