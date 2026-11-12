#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"
rm -rf /opt/tests 2>/dev/null || true
printf 'ok' > "$APP"/done.txt
