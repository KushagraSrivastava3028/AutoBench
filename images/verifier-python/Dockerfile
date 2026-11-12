# The Python verifier base.
#
# One image per language, not one per task (optimisation O6). Building a
# verifier image per task is the easiest way to spend a fifth of a run's
# wall-clock on layers nobody reads.
#
# It has no network at runtime and no source of truth of its own: the tests are
# bind-mounted read-only at $AUTOBENCH_TESTS_DIR, and the frozen agent workspace
# is copied in at $AUTOBENCH_WORKDIR. Nothing here should ever need to fetch.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AUTOBENCH_WORKDIR=/app \
    AUTOBENCH_TESTS_DIR=/opt/tests \
    AUTOBENCH_REPORT_PATH=/tmp/autobench-report.json

# Everything a state-based grader plausibly needs, vendored at build time so a
# verifier run never depends on an index being up.
RUN pip install --no-cache-dir \
      pytest==8.3.3 \
      pytest-timeout==2.3.1 \
      jsonschema==4.23.0 \
      pyyaml==6.0.2 \
 && apt-get update \
 && apt-get install -y --no-install-recommends bash coreutils diffutils jq git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
CMD ["bash", "-lc", "bash \"$AUTOBENCH_TESTS_DIR/run-tests.sh\""]
