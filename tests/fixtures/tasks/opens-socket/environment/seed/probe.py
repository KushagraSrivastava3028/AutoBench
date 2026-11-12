"""Probe for outbound connectivity. Expected to fail in every direction."""

import json
import socket

results = {}
for host, port in (("1.1.1.1", 53), ("pypi.org", 443), ("api.anthropic.com", 443)):
    try:
        socket.create_connection((host, port), timeout=2).close()
        results[host] = "REACHED"
    except OSError as exc:
        results[host] = f"blocked: {type(exc).__name__}"
print(json.dumps(results, indent=2))
