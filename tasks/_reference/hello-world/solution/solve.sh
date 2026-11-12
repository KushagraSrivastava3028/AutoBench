#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"
printf 'Hello, Autobench!' > "$APP"/greeting.txt
