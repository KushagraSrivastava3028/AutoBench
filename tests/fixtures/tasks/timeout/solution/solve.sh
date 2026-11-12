#!/usr/bin/env bash
# Fast path so the oracle gate still passes: the *agent* is what times out.
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"
printf 'done' > "$APP"/never.txt
