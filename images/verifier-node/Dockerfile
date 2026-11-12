# The Node verifier base. Same contract as verifier-python: no network at
# runtime, tests bind-mounted read-only, frozen workspace copied in.
FROM node:22-slim

ENV AUTOBENCH_WORKDIR=/app \
    AUTOBENCH_TESTS_DIR=/opt/tests \
    AUTOBENCH_REPORT_PATH=/tmp/autobench-report.json

RUN apt-get update \
 && apt-get install -y --no-install-recommends bash coreutils diffutils jq python3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g --no-fund --no-audit vitest@2.1.4

WORKDIR /app
CMD ["bash", "-lc", "bash \"$AUTOBENCH_TESTS_DIR/run-tests.sh\""]
