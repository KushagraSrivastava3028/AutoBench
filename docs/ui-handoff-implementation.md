# AutoBench UI handoff implementation

The existing Next.js application now follows `AutoBench_UI_Handoff.zip`. The
reference is retained in `design-reference/AutoBench_UI/`. Its simulated data is
not imported into the application.

Implemented:

- Neutral bento overview, rounded cards, responsive navigation, light/dark
  themes, visible keyboard focus, skip link, and native modal focus handling.
- Real active run, running/queued filters, recent runs and evidence-linked
  harness/budget alerts. No invented CI gate or oracle-drift alerts.
- Comparable benchmark history from matching suite, agent, model and k, with
  backend confidence intervals, 7/30-day selection, and accessible data table.
- Shared database-backed weekly budget and overview accounting. The target is
  advisory; per-run caps stop runs. Both screens use the same Monday–Monday UTC
  window and scored cost records.
- Browser run creation, cancellation/retry controls, symbol-and-color trial
  matrix, task search, keyboard operation, trial side sheet and full inspector.
- Paged, virtualized trajectory, filesystem diff, verdict, copyable replay,
  actual per-trial cost chart, and provenance for newly launched runs.
- Suite creation with pinned published versions. Task upload, static validation,
  draft persistence, isolated calibration and publication. Executing uploaded
  code requires Docker; local development cannot claim isolation.
- Uploads are limited to 32 MiB compressed, 128 MiB expanded and 5,000 members.
  Links, special files and extraction-root escapes are rejected; failed uploads
  clean up their temporary directory.
- Scoped API key creation/revocation, audit history, provider configuration,
  worker capacity, API key sign-in and an 8-hour HttpOnly session cookie.
- Generated OpenAPI response aliases, Next.js 16 / React 19.2, production build,
  and desktop/mobile Playwright acceptance coverage.
- Exact decimal seed fields prevent JavaScript from rounding 64-bit seeds.
  Replay uses the trial seed and captures the stopped workspace before grading.
- Task-clustered suite confidence intervals, incomplete-k protection,
  infrastructure-only error accounting, invalid-run leaderboard exclusion,
  authenticated artifacts, guarded worker persistence, and durable SSE resume.
- Strict backend typing and repository formatting/lint checks pass. CI checks
  schema drift and runs the browser acceptance suite against an offline stack.
- Run, suite and task actions respect API key scopes in the UI. Comparisons use
  scored, matching task versions; invalid harness runs cannot produce a result.

## Run and verify

Use Python 3.12+ and Node 22+ for the current checkout. From the repository root:

On Windows, `./scripts/start-local.ps1 -Build` starts the API, worker and
production UI in the background after dependencies and the catalogue are seeded.
It refuses to overwrite servers already listening on the application ports.

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m autobench.cli.main seed
.\.venv\Scripts\python.exe -m autobench.cli.main serve --host 127.0.0.1
# In a second terminal with the same PYTHONPATH:
.\.venv\Scripts\python.exe -m autobench.cli.main worker
# In a third terminal:
cd frontend
npm ci
npm run build
npm start
```

Apply `alembic upgrade head` before starting production API/worker processes.
SQLite development creates missing tables at startup. New migrations are
`0002_console`, `0003_transitions` and `0004_google_sessions`.

Set `AUTOBENCH_AUTH_REQUIRED=true` in **both** frontend and backend for protected
deployments. Configure Google Workspace sign-in and explicit initial administrator
emails, or bootstrap an administrator API key with the CLI, then sign in at
`/login`. The UI does not embed a shared administrator API key. Serve over HTTPS
behind the deployment reverse proxy. Uploaded task sources must be persisted in
a shared tasks directory writable by the authoring API and readable by workers.
Set `AUTOBENCH_PUBLIC_URL` in the frontend to the external HTTPS origin when
the reverse proxy rewrites the Host header.

```powershell
python -m pytest tests -q
cd frontend
npm run typecheck
npm run build
npm run test:browser
```

Browser tests use locally installed Chrome by default. They run against a
seeded API, one worker and the frontend on ports 8000/3000. Set
`AUTOBENCH_UI_URL` to change the frontend URL. The offline launch acceptance
case creates a labeled oracle/mock evaluation; it does not call a paid model.

## PRD v2 release status

This implementation is **not a claim that every PRD v2 production gate is
complete**. The original backend predates that PRD. Remaining release blockers:

| Requirement | Current state / required evidence |
|---|---|
| Projects, memberships, OIDC SSO and project RBAC | Google Workspace OIDC, hashed revocable sessions and workspace member roles are implemented. Project-scoped isolation/memberships remain outstanding; this is one shared workspace per deployment. |
| Full hardened sandbox policy | Docker implementation requires verification of gVisor, image signatures, read-only root defaults and writable task volumes. Local execution is development-only. |
| Production infrastructure target | Standalone EC2 Compose targets PostgreSQL 17, Valkey 8, native AWS S3 through IAM roles and Python 3.13. CloudFormation, Caddy TLS and backup configuration are provided; actual Docker builds, AWS deployment and restore acceptance are pending. |
| Reference corpus and performance acceptance | Four reference tasks; 50-task benchmark and measured throughput acceptance are outstanding. |
| Staging and recovery acceptance | 14-day SLO soak, multi-worker/Postgres crash tests, backup restore and DR evidence require a deployed environment. |
| End-user SSO and administration | Google sign-in and Settings → Members are implemented. Live OAuth client/Workspace allowlists/public domain must be configured and exercised in staging. |
| Monitoring completeness | Oracle drift and CI gate ingestion need backend sources. Provider/infra retry spend is not a complete invoice reconciliation ledger. |
| Fleet scale | Durable SSE currently checks the database every 500 ms per subscriber; validate scale and add shared wake-up notifications before a large rollout. |

Docker/Postgres deployment, SSO configuration and staging soak cannot be
certified by local UI screenshots or unit tests.

See [EC2 and Google deployment](ec2-google-deployment.md) for the chosen AWS EC2
Linux/Docker + Google Workspace OAuth 2.0 target, concrete commands and remaining
release acceptance. OAuth cryptographic verification is tested with real RSA
signatures and local certificates; flow and permission tests cover nonce/domain,
browser-bound state, expiry, logout and immediate membership changes.

An observed Windows development-executor limitation remains under investigation:
one full-suite run hit an intermittent pytest collection error in the
`fix-flaky-retry` verifier. Eight diagnostic repetitions passed. This does not
establish the PRD's 14-day staging SLO or deterministic Docker execution gate.
