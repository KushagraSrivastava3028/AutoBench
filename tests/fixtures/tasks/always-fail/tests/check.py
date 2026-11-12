"""Grade the final container state. Never the agent's output."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP = Path(os.environ.get("AUTOBENCH_WORKDIR", "/app"))


def emit(items: list[dict[str, object]]) -> None:
    total = sum(float(i["weight"]) for i in items) or 1.0
    reward = round(sum(float(i["weight"]) for i in items if i["passed"]) / total, 3)
    report = {
        "schema_version": "1.0",
        "items": items,
        "reward": reward,
        "binary_pass": reward >= 1.0,
        "verifier_version": "3.12.1",
    }
    json.dump(report, sys.stdout, indent=2)
    sys.exit(0 if report["binary_pass"] else 1)


# Deliberately unsatisfiable: no agent action can create this.
emit(
    [
        {
            "id": "impossible",
            "passed": False,
            "weight": 1.0,
            "detail": "this fixture is unsolvable by construction",
        }
    ]
)
