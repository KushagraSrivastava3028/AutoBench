# Architecture

> A trial is a pure function of (task version, agent version, model version, seed), and it leaves nothing behind.

Every decision below exists to protect that sentence.

## The five planes

| Plane | Process | Code | Scales by | If it dies |
|---|---|---|---|---|
| Client | browser, `autobench` CLI, CI | `frontend/`, `cli/` | — | nothing is lost |
| Control | `autobench serve` | `api/` | adding replicas (stateless) | launches pause; trials already running keep going |
| State | Postgres, Redis, MinIO | `db/`, `artifacts/` | a bigger machine | total outage; this is the only stateful tier |
| Execution | `autobench worker` × N | `worker/` | Docker host size and provider limits | its leases expire and the reaper re-queues its trials |
| Sandbox | one container per trial | `executor/` | the per-worker lane size | one trial is lost |

The control plane never runs a trial. `POST /v1/runs` plans the trials, commits them, and returns `202`. That split is what keeps the API fast, lets workers restart freely, and gives crash tolerance without a durable-workflow engine.

## A trial, stage by stage

`worker/pipeline.py` runs nine stages:

1. **lease**: a conditional `UPDATE … WHERE status='queued'`; skip-locked on Postgres
2. **image**: built once per `content_hash`, then served from cache (O1)
3. **create**: `cap_drop=ALL`, no network, CPU/memory/PID caps
4. **setup**: the optional `environment/setup.sh`, then a baseline snapshot
5. **agent**: the adapter drives the sandbox. The trial's timeout, tool-call cap and token cap apply here.
6. **freeze + diff**: pause the container, take a second snapshot, diff the two
7. **verify**: a *separate* container, with the frozen workspace copied in and the tests mounted read-only
8. **persist**: score row, trajectory and diff artifacts
9. **teardown**: runs in a `finally`, so it happens even when an earlier stage failed

### The retry rule

`core/errors.py::ErrorClass` separates two kinds of failure:

- **`infra_*` and `verifier_crash`** mean the harness failed. The trial is retried and **no `score` row is written**, so it cannot affect pass@k.
- **`agent_*`** means the agent failed: a timeout, a hit cap, or a wrong answer. It scores 0.0 and is never retried.

`POST /runs/{id}/retry-failed` can only re-queue infrastructure failures. Re-running agent failures until they pass is how a benchmark inflates its own numbers.

## Concurrency: three lanes, not one number

`worker/scheduler.py::ConcurrencyBudget`:

| Lane | What it limits | Default |
|---|---|---|
| `containers` | containers alive at once | `cores / task.cpus − 1` |
| `docker_ops` | concurrent daemon creates | 4 |
| `verifiers` | CPU-heavy verify bursts | `cores / 4` |
| provider | RPM/TPM, **shared across workers** in Redis | per model |

A single global concurrency number is always wrong for some part of the system. Each lane reports how much of the time it spends saturated, and the worker panel names the lane that is the bottleneck.

The provider lane (`models/ratelimit.py`) is a Lua sliding window that covers requests *and* tokens and is evaluated atomically. Without a limiter shared across workers, adding workers past about three turns throughput into 429 retry storms.

## Scheduling

`worker/planner.py` orders trials by **longest expected duration first** (LPT). The expected duration is the observed p50 when history exists, and the declared timeout otherwise. Attempts are **spread**: every attempt-0 trial runs before any attempt-1 trial, so partial progress is a sample across the whole suite rather than a finished slice of it.

## Crash recovery

Every leased trial carries `lease_expires_at`. Workers renew the lease from inside the agent loop, throttled to one write every 10 seconds. The reaper runs in the API every 30 seconds, re-queues expired leases, and marks a trial `infra_abandoned` after three expiries. The only critical section is a single conditional UPDATE.

Run counters use atomic SQL increments (`trials_done = trials_done + 1`) rather than an ORM read-modify-write. Concurrent workers otherwise lose updates.

## Executors

`executor/base.py::SandboxProvider` is the only interface the rest of the system sees.

- **`DockerProvider`** is production. It uses digest-pinned images, the seccomp profile in `profiles/`, and moves files as tar archives.
- **`LocalProvider`** uses a temporary directory and a subprocess. **It provides no isolation.** It exists so the scheduler, statistics, API and UI can run and be tested without a daemon. `Settings` refuses it outside `environment=development`.

A Kubernetes or microVM provider plugs in behind the same interface.

## Data

- **Postgres** holds the warehouse (`db/models.py`). Task versions are content-addressed, and suites pin versions rather than tasks.
- **Object storage** holds trajectories, diffs and reports, gzip-compressed above 16 KB. Bulk data stays out of Postgres.
- **Redis** carries the provider budget and pub/sub for SSE.

In development, all three can be replaced by SQLite, local files and in-process queues.
