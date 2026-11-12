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

proc = subprocess.run([sys.executable, str(APP / "rebuild.py")], capture_output=True,
                      text=True, cwd=str(APP), timeout=120)
index_path = APP / "store" / "index.json"
if not index_path.is_file():
    emit([{"id": "index_written", "passed": False, "weight": 1.0,
           "detail": f"store/index.json missing (exit {proc.returncode})"}])

try:
    index = json.loads(index_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    emit([{"id": "index_written", "passed": False, "weight": 1.0,
           "detail": f"index.json is not valid JSON: {exc}"}])

# Rebuild the ground truth straight from the data files.
truth = {}
for rec in sorted((APP / "store").glob("*.rec")):
    offset = 0
    for line in rec.read_bytes().split(b"\n"):
        if line.strip():
            key = line.split(b"\t", 1)[0].decode("utf-8")
            truth[key] = {"file": rec.name, "offset": offset}
        offset += len(line) + 1

items = [
    {"id": "all_keys_present",
     "passed": set(index) == set(truth), "weight": 0.4,
     "detail": f"missing={sorted(set(truth) - set(index))} "
               f"extra={sorted(set(index) - set(truth))}"},
    {"id": "offsets_correct",
     "passed": all(index.get(k) == v for k, v in truth.items()), "weight": 0.4,
     "detail": "every offset resolves to the start of its record line"},
    {"id": "no_dangling_files",
     "passed": all((APP / "store" / e["file"]).is_file()
                   for e in index.values() if isinstance(e, dict)),
     "weight": 0.2,
     "detail": "no index entry points at a file that does not exist"},
]
emit(items)
