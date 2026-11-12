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


import ast
import subprocess

items = []

# 1. Determinism: the same failure sequence must produce the same attempt count,
#    however long each attempt happens to take.
probe = """
import os
import sys
sys.path.insert(0, os.environ.get("AUTOBENCH_WORKDIR", "/app"))
from retry import retry

counts = []
for pad in (0.0, 0.02, 0.05):
    calls = []

    @retry(attempts=4, base_delay=0.0)
    def flaky():
        calls.append(1)
        if pad:
            import time as _t
            _t.sleep(pad)
        raise ValueError("boom")

    try:
        flaky()
    except ValueError:
        pass
    counts.append(len(calls))

print(counts)
"""
proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
try:
    counts = ast.literal_eval(proc.stdout.strip().splitlines()[-1])
except (SyntaxError, ValueError, IndexError):
    counts = []
deterministic = bool(counts) and len(set(counts)) == 1 and counts[0] == 4
items.append({"id": "retry_is_deterministic", "passed": deterministic, "weight": 0.5,
              "detail": f"attempt counts across varying latencies: {counts} "
                        f"(want four identical)"})

# 2. The existing contract still holds.
proc = subprocess.run([sys.executable, "-m", "pytest", "-q", str(APP / "test_retry_existing.py")],
                      capture_output=True, text=True, cwd=str(APP), timeout=120)
items.append({"id": "existing_tests_still_pass", "passed": proc.returncode == 0, "weight": 0.3,
              "detail": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip()
                        else f"pytest exited {proc.returncode}"})

# 3. No wall-clock sleep left in the hot path.
source = (APP / "retry.py").read_text(encoding="utf-8")
offending = []
for node in ast.walk(ast.parse(source)):
    if isinstance(node, ast.Call):
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name == "sleep":
            offending.append(node.lineno)
items.append({"id": "no_sleep_in_hot_path", "passed": not offending, "weight": 0.2,
              "detail": "clean" if not offending
                        else f"time.sleep at retry.py:{offending[0]}"})

emit(items)
