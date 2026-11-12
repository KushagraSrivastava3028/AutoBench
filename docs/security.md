# Security model

Treat every agent as untrusted, on every run. Published container-escape benchmarks report frontier-model escape rates of 15–35%. The threat model is therefore a motivated agent with a shell, not an accident.

## Container hardening (`executor/docker_provider.py::_host_config`)

| Control | Value |
|---|---|
| Capabilities | `CapDrop: [ALL]`, `CapAdd: []` |
| Privilege escalation | `no-new-privileges:true` |
| Syscalls | `profiles/autobench-default.json`: 244 allowed; mount, bpf, ptrace, setns, unshare, keyctl and 33 others explicitly denied |
| Privileged | `False`. No function in the module takes a parameter that could change this |
| Network (Mode A) | `none` |
| Resources | CPU, memory (swap disabled), PIDs, `nofile`/`nproc` ulimits |
| `/tmp` | tmpfs with `noexec,nosuid,nodev` |

## Refused at publish time

Checked in both the schema (`taskspec/schema.py`) and the security lint (`taskspec/lints.py`):

- a mounted Docker socket, whatever the justification
- `privileged: true`
- `network_mode: host`, `pid: host`
- `CAP_SYS_ADMIN`, `CAP_SYS_PTRACE`, `CAP_NET_ADMIN`, `CAP_SYS_MODULE`, `CAP_SYS_RAWIO`
- host bind mounts outside `/app`
- anything that looks like a credential in `environment.env`

The verifier's network is hard-coded to `none`. A verifier with network access could download its own answer.

## Test isolation

Tests are **never** present in the agent's container. They are mounted read-only into a separate verifier container after the agent's container is frozen. `tests/integration/test_pipeline.py::test_tests_are_absent_during_the_agent_phase` checks that directly.

## Egress (Mode B)

`images/proxy/proxy.py` runs as a CONNECT proxy with an FQDN allowlist.

- **Exact match or dot-suffix only.** `api.anthropic.com.evil.test` is refused.
- **TLS passes through untouched.** The proxy sees hostnames, never plaintext.
- **The real provider key stays in the proxy.** The agent's container holds a per-trial token that stops working when the trial ends.

## Secrets

- Provider keys live in the worker's environment. They are never stored in Postgres, a task image or a trajectory.
- `core/security.py::redact` runs **when data is written**: on known key shapes (`sk-ant-`, `sk-proj-`, `AIza`, `ghp_`…), on `*KEY/TOKEN/SECRET=` assignments, on auth headers, and on any high-entropy string of 32 characters or more. Hex digests are kept, because they are how you debug.
- 422 responses never echo the submitted value.
- 500 responses carry a generic message; the exception text goes to the logs only.

## Control plane

API keys use Argon2id hashes with a plain-text prefix, so lookup is an index hit rather than a hash-every-row scan. Scopes are hierarchical: `admin ⊃ author ⊃ run ⊃ read`. Every mutating call writes an `audit_log` row. Production mode (`AUTOBENCH_ENVIRONMENT=production`) refuses to start with `executor=local`, `queue=memory` or authentication disabled.

## Worker ↔ Docker

The compose stack gives workers a **filtered socket proxy** (`tecnativa/docker-socket-proxy`) over TCP, never the socket itself. That proxy allows only container, image, exec, build, volume and network calls. System, secrets, swarm and plugin endpoints are denied.
