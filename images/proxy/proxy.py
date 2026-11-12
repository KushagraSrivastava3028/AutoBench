"""The egress proxy sidecar (Mode B only).

When an agent CLI runs *inside* the task container, it needs a route to a model
API — and that is the one mode where a hostile or confused agent has both code
execution and a network path. The mitigation is this process:

* The task container joins a private bridge whose **only** reachable peer is
  this proxy. There is no default route to anywhere else.
* This proxy enforces an FQDN allowlist. Everything else is refused and logged.
* **The provider credential lives here, not in the container.** The agent
  authenticates to the proxy with a per-trial token that dies with the trial,
  and the proxy swaps it for the real key on the way out. A leaked transcript
  therefore leaks a dead token rather than your API key.

It is a CONNECT proxy so TLS stays end-to-end: the proxy sees the hostname in
the CONNECT line and the byte count, never the plaintext. That is deliberate —
a proxy that could read the prompts would be a more attractive target than the
thing it protects.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field

logging.basicConfig(
    level=os.environ.get("AUTOBENCH_PROXY_LOG", "INFO"),
    format="%(asctime)s %(levelname)-7s egress: %(message)s",
)
logger = logging.getLogger("autobench.proxy")

DEFAULT_ALLOWLIST = ("api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com")
CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 600.0


@dataclass(slots=True)
class Policy:
    allowlist: frozenset[str]
    trial_token: str | None = None
    allowed: int = 0
    refused: int = 0
    refused_hosts: set[str] = field(default_factory=set)

    def permits(self, host: str) -> bool:
        """Exact match, or a subdomain of an allowlisted apex.

        Deliberately not a substring check: ``api.anthropic.com.evil.test``
        contains an allowlisted string and must still be refused.
        """
        host = host.lower().strip().rstrip(".")
        if host in self.allowlist:
            return True
        return any(host.endswith(f".{entry}") for entry in self.allowlist)


async def handle(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, policy: Policy
) -> None:
    peer = writer.get_extra_info("peername")
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=CONNECT_TIMEOUT)
        if not request_line:
            return

        parts = request_line.decode("latin-1").split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            await _refuse(writer, 405, "only CONNECT is proxied")
            return

        host, _, port_text = parts[1].partition(":")
        port = int(port_text or 443)

        # Drain the rest of the CONNECT headers, including the trial token.
        token: str | None = None
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=CONNECT_TIMEOUT)
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode("latin-1").partition(":")
            if name.strip().lower() == "proxy-authorization":
                token = value.strip().removeprefix("Bearer ").strip()

        if policy.trial_token and token != policy.trial_token:
            policy.refused += 1
            logger.warning("refused %s: bad or missing trial token", host)
            await _refuse(writer, 407, "a valid per-trial token is required")
            return

        if not policy.permits(host):
            policy.refused += 1
            policy.refused_hosts.add(host)
            logger.warning("refused %s:%d (not on the allowlist)", host, port)
            await _refuse(writer, 403, f"{host} is not on the egress allowlist")
            return

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("upstream %s:%d unreachable: %s", host, port, exc)
            await _refuse(writer, 502, "upstream unreachable")
            return

        policy.allowed += 1
        logger.info("allowed %s:%d from %s", host, port, peer)

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        # TLS passes through untouched: the proxy is a policy point, not a MITM.
        await asyncio.gather(
            _pump(reader, upstream_writer),
            _pump(upstream_reader, writer),
            return_exceptions=True,
        )
    except (asyncio.TimeoutError, ConnectionResetError):
        pass
    except Exception as exc:  # pragma: no cover
        logger.warning("proxy error: %s", exc)
    finally:
        for stream in (upstream_writer, writer):
            if stream is not None:
                stream.close()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(65_536), timeout=IDLE_TIMEOUT)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass


async def _refuse(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    body = reason.encode()
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )
    try:
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass


async def main() -> int:
    parser = argparse.ArgumentParser(description="Autobench egress allowlist proxy")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - inside a private bridge
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="FQDN to permit. Repeatable. Defaults to the known model APIs.",
    )
    args = parser.parse_args()

    allowlist = frozenset(
        args.allow
        or [h for h in os.environ.get("AUTOBENCH_EGRESS_ALLOWLIST", "").split(",") if h]
        or DEFAULT_ALLOWLIST
    )
    policy = Policy(
        allowlist=allowlist,
        trial_token=os.environ.get("AUTOBENCH_TRIAL_TOKEN") or None,
    )

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, policy), args.host, args.port
    )
    logger.info(
        "listening on %s:%d; allowlist=%s; token=%s",
        args.host,
        args.port,
        ", ".join(sorted(allowlist)),
        "required" if policy.trial_token else "not configured",
    )
    async with server:
        await server.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
