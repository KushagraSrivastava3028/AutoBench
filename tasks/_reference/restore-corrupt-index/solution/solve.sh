#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"

cat > "$APP"/rebuild.py <<'EOF'
"""Rebuild store/index.json from the authoritative .rec files."""
from __future__ import annotations

import json
import os
from pathlib import Path

STORE = Path(os.environ.get("AUTOBENCH_WORKDIR", "/app")) / "store"


def main() -> None:
    index: dict[str, dict[str, object]] = {}
    for record_file in sorted(STORE.glob("*.rec")):
        offset = 0
        for line in record_file.read_bytes().split(b"\n"):
            if line.strip():
                key = line.split(b"\t", 1)[0].decode("utf-8")
                # Last occurrence wins, so a later file overwrites an earlier one.
                index[key] = {"file": record_file.name, "offset": offset}
            offset += len(line) + 1

    (STORE / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
EOF
python3 "$APP"/rebuild.py
