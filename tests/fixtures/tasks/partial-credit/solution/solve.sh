#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"
printf '1' > "$APP"/alpha.txt
printf '2' > "$APP"/beta.txt
printf '3' > "$APP"/gamma.txt
