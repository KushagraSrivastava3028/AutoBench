# Operations

## Sizing (one 16-core, 64 GB host)

| Service | Replicas | CPU | RAM |
|---|---|---|---|
| api | 2 | 1 | 1G |
| worker | 4 | 3 | 12G |
| postgres | 1 | 2 | 8G |
| redis / minio | 1 | 1 | 2G |

For `max_containers` per worker, use `(cores_available / task.cpus) − 1`.

**Disk is the constraint people forget.** A 500-task suite's image cache plus container diffs needs 200–400 GB. Budget 1 TB and schedule `make prune`.

## Migrations

```bash
make migrate            # alembic upgrade head
make migration m="..."  # autogenerate
```

CI runs upgrade → downgrade → upgrade against Postgres 16. It fails if autogenerate finds model changes that have no migration.

## Metrics (`GET /metrics`)

| Metric | Why |
|---|---|
| `autobench_trials_total{status}` | throughput |
| `autobench_trial_errors_total{error_class}` | infrastructure failures versus agent failures |
| `autobench_queue_depth` | backlog |
| `autobench_lane_utilisation{lane}` | whether runs are limited by containers or by the model API |
| `autobench_worker_heartbeat_age_seconds` | liveness |
| `autobench_containers_active` | leak detection |
| `autobench_cost_usd_total` | spend |

## Alerts (`ops/alerts.yml`)

| Alert | Response |
|---|---|
| **InfraErrorRateHigh** (>5%) | **Stop reporting numbers.** The harness is giving wrong results. |
| ProviderThrottling (>2% 429s) | Lower `PROVIDER__REQUESTS_PER_MINUTE` or add workers more slowly |
| ContainerLeak | Teardown is failing. Run `make prune` and check the reaper |
| WorkerStale (>120s) | Its leases will be reaped. Check the host |
| QueueStalled | No workers are alive, or trials are deadlocked |
| CostSpike (>2× mean) | Probably a runaway loop. Set `budget_usd` on runs |

## Runbook

**A run is stuck at N/M.** Check `GET /v1/stats/workers`. If there are no fresh heartbeats, start a worker. Leased trials whose leases expired are re-queued within 30 seconds.

**Many `infra_*` errors.** Check them by class: `infra_image` means a broken Dockerfile or the registry, `infra_docker` means the daemon, `infra_provider` means an API key or quota problem. Fix the cause, then `POST /runs/{id}/retry-failed`. That call only re-queues infrastructure failures.

**Scores changed and nothing obvious changed.** Compare the `content_hash` of the task versions. Then run `bench-harness` with the oracle. If the oracle's score moved, the harness drifted, not the model.

## Measuring the harness

```bash
make bench     # oracle agent, N=1,2,4,8, 3 repeats → bench/harness-<date>.json
```

The nightly workflow re-runs this and commits the result. Acceptance criterion G2 requires at least a 40% wall-clock reduction at N=8, a 429 rate below 1%, and zero infrastructure errors.
