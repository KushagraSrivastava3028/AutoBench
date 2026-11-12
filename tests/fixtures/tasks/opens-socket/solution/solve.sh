#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"
python3 "$APP"/probe.py > "$APP"/egress.json
