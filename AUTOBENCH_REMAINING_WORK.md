# AutoBench — Remaining Work

Updated: 12 November 2026

Production target: **AWS EC2 running Linux and Docker, with Google Workspace OAuth 2.0 / OIDC sign-in.**

The UI and core evaluation workflows are largely implemented. Google sign-in, workspace roles and an EC2 deployment package exist. AutoBench has **not been deployed or certified for production**. Unchecked items below are unfinished implementation or verification work; configuration files and passing local tests alone do not satisfy deployed acceptance criteria.

Source of requirements: [AutoBench PRD](AUTOBENCH_PRD%20(1).md), especially sections 3.3, 14–18 and 22.

## 1. Finish verification of the current implementation — P0

- [ ] Rerun the complete backend test suite against the final source. The earlier 157-test suite passed, and 14 Google authentication tests passed separately; the latest combined run stopped on a test enum typo that has since been corrected. A complete green run is still required.
- [ ] Complete migration upgrade → downgrade → upgrade validation and schema drift checks on a fresh database after the latest correction to `0004_google_sessions`.
- [ ] Investigate the SQLite `BIGINT` / `AutoBigInt` drift reported for `audit_log.id` and `trial_event.id`; verify audit and trajectory inserts work against a migrated database. Validate the migration chain separately on PostgreSQL 17.
- [ ] Restart the local API, worker and frontend from the final source/build; background processes may still be serving an earlier version.
- [ ] Run the full desktop/mobile browser suite, including the new Members and Google sign-in UI coverage. Confirm the real oracle/mock run completes and its seed, verdict, trajectory and replay command are correct.
- [ ] Review light/dark screenshots against the UI handoff and finish accessibility, keyboard, responsive overflow, error-state and empty-state checks.
- [ ] Confirm final Python lint/format/type checks, generated API contract consistency, frontend typecheck and production build all pass together.
- [ ] Resolve or characterize the intermittent Windows `fix-flaky-retry` verifier collection failure. Diagnostic repetitions passed, but its cause has not been established; validate deterministic behavior in the Linux Docker executor.

**Exit evidence:** saved test/build results, clean migration checks, final browser screenshots and a successful end-to-end offline evaluation.

## 2. Supply production configuration — P0, requires deployment values

- [ ] Select the AWS region, VPC, subnet, reviewed Linux AMI and instance size.
- [ ] Provide the production hostname and configure DNS.
- [ ] Set the allowed Google Workspace domain(s) and initial administrator email(s).
- [ ] Create/configure the Google OAuth Web application client and register the exact HTTPS callback: `https://YOUR_HOST/api/auth/google/callback`.
- [ ] Store the OAuth client secret, database/Valkey passwords and any model-provider keys securely on the deployment system. Do not commit them or put them in this document.
- [ ] Populate the protected production environment file with the public origin, OAuth configuration, connection URLs, S3 bucket and region.

**Exit evidence:** complete deployment configuration with no placeholders and secrets supplied through protected deployment storage.

## 3. Deploy and validate the EC2 stack — P0

- [ ] Validate the CloudFormation template with AWS in the selected account/region, review resource costs and deploy it.
- [ ] Install and verify Docker Engine, Compose, gVisor `runsc`, the AWS CLI and SSM access on the host.
- [ ] Build and exercise the Python 3.13 application, frontend and verifier images on Linux. Docker builds have not been validated locally because no Docker daemon is available here.
- [ ] Pin reviewed image digests for an immutable release; current version-family image tags are not a release lock.
- [ ] Render the standalone EC2 Compose configuration using the actual environment and run `scripts/validate_ec2_config.py`. Local rendering with test values passed; that does not validate real credentials or infrastructure.
- [ ] Provision persistent task storage with the documented ownership and matching host/container paths. Apply migrations before starting application processes and seed the catalogue.
- [ ] Verify PostgreSQL 17, Valkey 8, S3 access through the EC2 IAM role, TLS issuance/renewal, health/readiness checks and worker heartbeats.
- [ ] Verify externally that only intended HTTP/HTTPS ports are reachable; backend, database, Valkey and Docker-control ports must remain private.
- [ ] Bring the single-host layout up to the PRD's two API replicas, with tested health routing and restart behavior. The initial EC2 package currently runs one API service instance.
- [ ] Exercise live Google login, wrong-domain rejection, role changes, disabled accounts, logout, expiry and independent administrator recovery.

**Exit evidence:** a working HTTPS staging URL, deployed service health, live SSO/role results and a successful real evaluation on EC2.

## 4. Complete sandbox and supply-chain security — P0

- [ ] Verify gVisor is actually used and missing-runtime failures cannot silently fall back to an unrestricted runtime.
- [ ] Complete and test the PRD's read-only root filesystem and writable workspace policy, non-root/user-namespace behavior, resource limits and seccomp controls.
- [ ] Isolate task image builds. Task Dockerfiles execute code during builds and must not reach instance metadata, secrets or control-plane services.
- [ ] Complete Docker API authorization/ownership controls. The existing socket proxy filters endpoint classes; it does not enforce container ownership.
- [ ] Add/enforce cosign signature verification and Syft SBOMs for application and published task images; production workers must reject unsigned images.
- [ ] Verify hostile fixtures cannot mount sockets, request privilege, escape isolation, exhaust the host or leave resources behind.
- [ ] If network-enabled tasks are supported, implement and test the PRD egress allowlist and denial of metadata, private/control networks and credentials. The EC2 package currently permits only network-isolated task containers.
- [ ] Audit dependency locking, vulnerability scan gates, secret redaction and log handling against the PRD release requirements.

**Exit evidence:** passing Linux/Docker adversarial tests and a reviewed security policy. Keep task authoring limited to trusted administrators until these gates pass.

## 5. Complete project access, budgets and audit requirements — P0 for full PRD compliance

- [ ] Implement projects and project memberships, project-scoped API keys and data isolation across runs, tasks, suites, artifacts, exports and live streams. Current roles apply to one shared deployment workspace.
- [ ] Test the complete viewer/runner/author/admin role matrix and cross-project access denial at the API boundary.
- [ ] Audit and complete API-key expiry and per-key control-plane rate limiting.
- [ ] Enforce append-only audit storage with database permissions; verify every mutation is attributed and audit history is exportable.
- [ ] Implement the PRD's project monthly budget and launch-blocking behavior, with an audited administrator override. The implemented weekly workspace target is advisory; it does not replace this requirement.
- [ ] Verify launch cost estimates, per-run warning/cancellation thresholds and accounting for provider/infra retry spend.

**Exit evidence:** project isolation tests, role/key tests, database audit restrictions and budget enforcement tests.

## 6. Complete the reference corpus and performance acceptance — P1, required before GA

- [ ] Expand the reference corpus from **4 to 50 tasks**, with documented versions and coverage.
- [ ] Calibrate every reference task: oracle reward 1.0, null reward 0.0, hermetic execution and deterministic results.
- [ ] Demonstrate an unattended `suite × agent × model`, `k = 5` evaluation with pass@1, pass@5 and pass^5 confidence intervals.
- [ ] Measure at least **40% wall-clock reduction at concurrency 8 versus 1**, with three repeats, and retain benchmark evidence under `bench/` per the PRD.
- [ ] Run the specified load scenario: 1,000 queued trials, 16 workers and 50 concurrent UI users. Verify queue drainage and API latency targets.
- [ ] Verify reported costs against actual provider invoices within the PRD's 2% tolerance.
- [ ] Exercise the CI regression gate with a seeded regression and retain evidence that it fails correctly.

## 7. Complete operational monitoring and durability acceptance — P1, required before GA

- [ ] Implement/verify the nightly integrity reconciler: score uniqueness, stuck trials, terminal counters and run-cost totals; surface violations to administrators.
- [ ] Connect real nightly oracle-drift and CI-gate results to monitoring/UI where required. Do not substitute simulated alerts.
- [ ] Verify worker death, lease expiry, API restart, PostgreSQL interruption, Valkey loss and SSE reconnection produce zero lost trials and zero double scores.
- [ ] Verify no orphaned containers remain after ten minutes.
- [ ] Load-test durable SSE. Current subscribers poll the database every 500 ms; add shared wake-up notifications if needed to meet capacity/freshness targets.
- [ ] Exercise runbooks RB-1 through RB-8 and record outcomes.
- [ ] Run staging for **14 consecutive days**, recording all PRD section 3.3 SLOs:

| SLO | PRD target |
|---|---|
| API availability | 99.5% monthly target; include staging evidence |
| Read API latency | p95 below 300 ms |
| Run creation latency | p95 below 800 ms |
| Trial-to-browser update freshness | p95 below 2 seconds |
| Harness infrastructure errors | Below 2% of trials per run |
| Trial durability | Zero lost or double-scored trials |
| Nightly reference oracle | Exactly 1.000 |
| Container hygiene | Zero orphaned containers after 10 minutes |

## 8. Complete backups, recovery and release operations — P1, required before GA

- [ ] Install, enable and monitor the provided application-backup service/timer and verify successful database/task uploads to S3.
- [ ] Implement continuous PostgreSQL WAL archiving plus the required base-backup strategy. Hourly logical dumps and daily EBS snapshots alone do **not** meet the PRD database RPO of five minutes.
- [ ] Complete the required independent artifact backup/synchronization and retention policy; same-bucket versioning alone is not an independent recovery copy.
- [ ] Perform a real isolated restore drill, including database, task sources, historical scores, trajectories and artifacts. Run the reconciler after restoration.
- [ ] Measure recovery against the PRD targets: database RPO ≤5 minutes / RTO ≤30 minutes; artifacts RPO ≤24 hours / RTO ≤1 hour; API recovery target one minute.
- [ ] Automate monthly restore drills and retain their evidence.
- [ ] Test expand/migrate/contract releases, worker draining, supported API/worker version skew and rollback without data loss.
- [ ] Have a fresh engineer deploy the single-host topology and complete author → run → inspect using only the deployment documentation and user guide; fix any missing steps.
- [ ] Publish a final release acceptance record mapping every PRD section 22 gate to actual evidence before calling AutoBench production-ready.

## Suggested execution order

1. Finish local tests, migrations and browser acceptance.
2. Complete security and project/budget/audit implementation while collecting deployment configuration.
3. Deploy a protected EC2 staging environment and exercise live Google SSO.
4. Complete the corpus, benchmarks, load/chaos tests and recovery drills.
5. Run the staging soak, close all release gates and promote a reviewed immutable release.

## Related documents

- [UI implementation and current limitations](docs/ui-handoff-implementation.md)
- [EC2 and Google deployment guide](docs/ec2-google-deployment.md)
- [Standalone EC2 Compose configuration](docker-compose.ec2.yml)
- [EC2 CloudFormation foundation](ops/ec2/stack.json)
- [Production environment template — no real secrets](ops/ec2/production.env.example)
