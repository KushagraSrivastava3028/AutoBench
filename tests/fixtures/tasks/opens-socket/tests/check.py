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


# This fixture asserts a property only a real sandbox can provide. Under the
# local executor there is no network namespace, so it reports honestly rather
# than passing itself.
if os.environ.get("AUTOBENCH_EXECUTOR") == "local":
    emit(
        [
            {
                "id": "egress_blocked",
                "passed": False,
                "weight": 1.0,
                "detail": "requires the Docker executor: the local executor has no "
                "network namespace and cannot enforce network='none'",
            }
        ]
    )

report = APP / "egress.json"
blocked = False
if report.is_file():
    try:
        results = json.loads(report.read_text(encoding="utf-8"))
        blocked = all(str(v).startswith("blocked") for v in results.values())
    except json.JSONDecodeError:
        blocked = False
emit(
    [
        {
            "id": "egress_blocked",
            "passed": blocked,
            "weight": 1.0,
            "detail": "every outbound connection attempt must fail with network='none'",
        }
    ]
)
