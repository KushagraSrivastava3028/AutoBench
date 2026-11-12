# The egress allowlist sidecar (Mode B only).
#
# Minimal by design: it holds the real provider credential, so it should have
# the smallest possible attack surface. No shell tooling, no package manager at
# runtime, non-root.
FROM python:3.12-slim

COPY proxy.py /opt/proxy.py

RUN useradd --uid 10002 --no-create-home proxy
USER proxy

EXPOSE 8080
ENTRYPOINT ["python", "/opt/proxy.py"]
