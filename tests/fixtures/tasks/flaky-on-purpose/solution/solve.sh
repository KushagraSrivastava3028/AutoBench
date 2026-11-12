#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"
# The oracle always wins; only a real agent sees the flake.
printf 'heads' > "$APP"/coin.txt
