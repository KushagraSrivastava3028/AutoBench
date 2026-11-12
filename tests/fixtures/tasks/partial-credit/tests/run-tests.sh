#!/usr/bin/env bash
# Verifier entrypoint. Runs in a clean container with the tests mounted
# read-only and the frozen agent workspace at $AUTOBENCH_WORKDIR.
#
# Paths come from AUTOBENCH_* variables rather than hard-coded absolutes so the
# same task runs unchanged under the Docker executor and the local one. The
# defaults are the container paths, so a task still works if run by hand.
set -uo pipefail

APP="${AUTOBENCH_WORKDIR:-/app}"
TESTS="${AUTOBENCH_TESTS_DIR:-/opt/tests}"
REPORT="${AUTOBENCH_REPORT_PATH:-/tmp/autobench-report.json}"

mkdir -p "$(dirname "$REPORT")"
cd "$APP" || exit 3

python3 "$TESTS/check.py" > "$REPORT"
status=$?
cat "$REPORT"
exit $status
