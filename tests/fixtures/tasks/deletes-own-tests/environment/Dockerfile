# Digest-pinned: a floating tag means the environment drifts between runs and
# the score moves for reasons that have nothing to do with the agent.
FROM python:3.12-slim@sha256:0b1e0b3f1e1f2a6d9c1c2f6a8b5e4d3c2b1a0f9e8d7c6b5a4938271605f4e3d2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY environment/seed/ /app/

