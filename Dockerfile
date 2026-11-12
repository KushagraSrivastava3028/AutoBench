# Autobench control plane and worker.
#
# One image serves both roles — they share every module and differ only in
# entrypoint. Two images would mean two dependency sets drifting apart, and a
# worker running different scoring code from the API is the exact failure this
# project exists to prevent.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# The worker shells out to `docker build`, so it needs the client (never the
# daemon, and never the socket — see docs/security.md).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git bash \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && chmod a+r /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
      https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /srv/autobench

# Dependencies first: the layer cache then survives every source edit.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN uv pip install --system --no-cache -e ".[providers,export]"
COPY alembic.ini ./
COPY profiles/ ./profiles/

# Non-root by default. The worker talks to Docker over a TCP proxy rather than
# a mounted socket, so it needs no privileged group membership.
RUN useradd --create-home --uid 10001 autobench \
 && mkdir -p /srv/autobench/.autobench \
 && chown -R autobench:autobench /srv/autobench
USER autobench

ENV AUTOBENCH_STATE_DIR=/srv/autobench/.autobench

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

EXPOSE 8000
CMD ["autobench", "serve"]


# ---------------------------------------------------------------------------
FROM base AS worker
# Same image, different entrypoint. No healthcheck: a worker has no port, and
# its liveness is the heartbeat row the API's WorkerStale alert watches.
HEALTHCHECK NONE
CMD ["autobench", "worker"]
