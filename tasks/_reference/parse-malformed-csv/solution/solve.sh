#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"

cat > "$APP"/summarise.py <<'EOF'
"""Summarise a ragged CSV export, skipping rows that cannot be parsed."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

APP = Path(os.environ.get("AUTOBENCH_WORKDIR", "/app"))
SOURCE = APP / "orders.csv"
TARGET = APP / "summary.json"


def main() -> None:
    rows = 0
    total = 0
    currencies: set[str] = set()

    # utf-8-sig strips the BOM the legacy exporter leaves behind.
    with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        amount_at = header.index("amount_cents")
        currency_at = header.index("currency")

        for record in reader:
            if len(record) <= amount_at:
                continue
            try:
                amount = int(record[amount_at].strip())
            except ValueError:
                continue
            rows += 1
            total += amount
            if len(record) > currency_at and record[currency_at].strip():
                currencies.add(record[currency_at].strip())

    TARGET.write_text(
        json.dumps({"rows": rows, "total_cents": total,
                    "currencies": sorted(currencies)}, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
EOF
python3 "$APP"/summarise.py
