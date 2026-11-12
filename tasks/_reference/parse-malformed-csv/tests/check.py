"""Grade the final container state. Never the agent's output."""
from __future__ import annotations

import json
import math
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


import subprocess

proc = subprocess.run([sys.executable, str(APP / "summarise.py")], capture_output=True,
                      text=True, cwd=str(APP), timeout=60)
summary_path = APP / "summary.json"
items = []

if not summary_path.is_file():
    emit([{"id": "summary_written", "passed": False, "weight": 1.0,
           "detail": f"summary.json not written (exit {proc.returncode})"}])

try:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    emit([{"id": "summary_written", "passed": False, "weight": 1.0,
           "detail": f"summary.json is not valid JSON: {exc}"}])

# Rows 1, 2, 4, 5, 6 and 8 have a parseable amount_cents.
#   row 3 -> "not-a-number"      skipped
#   row 5 -> truncated after amount_cents: the amount is still unambiguous, so
#            it counts; it just contributes no currency
#   row 6 -> a trailing extra column, amount_cents still unambiguous
#   row 7 -> empty amount        skipped
expected_total = 1050 + 2500 + 7325 + 4000 + 1500 + 9999
items.append({"id": "row_count", "passed": summary.get("rows") == 6, "weight": 0.3,
              "detail": f"rows = {summary.get('rows')} (want 6)"})
items.append({"id": "total_cents",
              "passed": math.isclose(float(summary.get("total_cents", -1)),
                                     expected_total, rel_tol=0, abs_tol=0),
              "weight": 0.4,
              "detail": f"total_cents = {summary.get('total_cents')} (want {expected_total})"})
items.append({"id": "currencies",
              "passed": summary.get("currencies") == ["EUR", "GBP", "USD"],
              "weight": 0.3,
              "detail": f"currencies = {summary.get('currencies')} (want ['EUR','GBP','USD'])"})
emit(items)
