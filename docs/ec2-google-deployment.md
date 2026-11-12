# EC2 and Google Workspace deployment

This package targets one trusted organization's workspace on a Linux Docker EC2
host. The UI, real evaluation workflow, Google OIDC sessions, roles and deployment
configuration are implemented. Deployment and the PRD's release acceptance are
separate steps. Multi-project isolation is not implemented; do not use this as a
multi-tenant service. Untrusted task authoring remains gated on the sandbox review
described below.

## Foundation

`ops/ec2/stack.json` is a CloudFormation foundation to review and deploy in your
chosen region. Supply an existing VPC, a public subnet in that VPC, and a reviewed
Ubuntu 24.04 LTS x86_64 AMI ID. The default `m7i.4xlarge` is the PRD's 16-vCPU /
64-GiB starting point; check availability and cost before provisioning.

It creates an EC2 host with encrypted gp3 EBS, IMDSv2 required, an instance role
limited to its dedicated S3 bucket plus SSM, an Elastic IP, and ingress only on
80/443. There is no SSH ingress. S3 has encryption, versioning, public access
blocked and TLS required. Daily EBS snapshots retain fourteen copies. The root
volume and S3 bucket are retained when the host/stack is removed and continue to
incur storage charges. EBS snapshots are crash-consistent; application backups
are required independently.

Validate with `aws cloudformation validate-template --template-body
file://ops/ec2/stack.json` before deployment. This workspace has neither AWS
credentials nor the AWS CLI, so the template has not been submitted to AWS.
The stack does not install the application or create Google credentials.

## Prepare the host

Connect with AWS Systems Manager Session Manager. Install Docker Engine and the
Compose plugin from Docker's official Ubuntu repository, Python 3, the AWS CLI,
and gVisor `runsc` using their official installation guides. Register `runsc` in
the Docker daemon, restart Docker, and verify `docker info` lists it. The Compose
package explicitly requests `runsc`; missing runtime must fail trials rather
than silently falling back to `runc`.

Copy this checkout to `/srv/autobench/app`. Put persistent task sources in
`/srv/autobench/tasks`, initially copying `tasks/_reference` there. Give UID 10001
ownership of that task directory, since the API imports task versions there.
Keep the host and container paths identical so the host Docker daemon can bind
the verifier's test directory. Keep shell scripts with LF line endings.

Point the production DNS hostname at the stack's Elastic IP. Allow outbound
HTTPS for Google, S3, SSM, model providers and image/package downloads. Workload
containers have no network in this package. Any future egress mode must first
block both metadata addresses (`169.254.169.254`, `fd00:ec2::254`) and all control
networks; an IMDS hop limit alone is not containment. Docker image builds also
require review because a task Dockerfile executes build instructions.

Pin reviewed container image digests in your release manifest before production
promotion. Version-family tags in this source are a starting configuration,
not an immutable release lock.

## Configure Google

In Google Cloud, configure an internal Google Workspace OAuth consent application
and a Web application OAuth client. Request only `openid email profile`. Register
exactly `https://YOUR_HOST/api/auth/google/callback` as an authorized redirect
URI. Sign-in does not request offline access or retain Google access/refresh
tokens.

Copy `ops/ec2/production.env.example` to `ops/ec2/production.env` on the host and
set mode `600`. Fill in the client ID/secret, the Workspace domain allowlist, the
initial administrator email allowlist, the HTTPS public origin and hostname,
the S3 bucket stack output, and region. Use separate random hexadecimal database
and Valkey passwords, also substituting them in their connection URLs. Store the
Google secret in your deployment's secret manager and materialize the protected
environment file during release; never send it in chat or commit it.

The application validates Google's signature, issuer, client audience, expiry,
nonce, verified email and signed `hd` domain. OAuth state is one-use, expires in
ten minutes, and is bound to the initiating browser with an HttpOnly cookie and
PKCE. The permanent identity is Google `sub`. Session tokens are random, stored
only as hashes in PostgreSQL, and sent in Secure HttpOnly SameSite=Lax cookies.
Sessions expire after eight hours by default. Logout revokes the server session.

Bootstrap email allowlisting grants admin only when an identity is first
created; it never promotes an existing identity on every login. New members
otherwise receive viewer access. Administrators use Settings → Members to grant
runner, author or admin roles, or disable users. Permissions are looked up on
every request. Disabling a user revokes all their sessions. An administrator
cannot remove their own admin role or disable themselves; the last active admin
cannot be removed. Keep an independently held admin API key for recovery using
the existing `autobench keys create` CLI. API keys remain scoped automation
credentials; the UI never forwards all users through one shared admin key.

## Build and start

Run from `/srv/autobench/app`:

```bash
chmod 600 ops/ec2/production.env
docker compose --env-file ops/ec2/production.env -f docker-compose.ec2.yml config --format json \
  | python3 scripts/validate_ec2_config.py
docker build -t autobench/verifier-python:3.12 images/verifier-python
docker build -t autobench/verifier-node:22 images/verifier-node
docker compose --env-file ops/ec2/production.env -f docker-compose.ec2.yml build
docker compose --env-file ops/ec2/production.env -f docker-compose.ec2.yml up -d
docker compose --env-file ops/ec2/production.env -f docker-compose.ec2.yml run --rm migrate autobench seed
```

This standalone file replaces development Compose; do not merge the files.
Only Caddy publishes host ports. Database, Valkey, Docker proxy, API and UI are
private Docker services. PostgreSQL 17 and Valkey 8 persist on named volumes;
artifacts use native S3 via the EC2 instance role, with no static AWS credentials.
Schema migration completes before API/worker startup. The seed command registers
the reference corpus and offline oracle/mock entries without provider charges.
The Python application image uses 3.13; reference verifier images remain pinned
to the task's declared runtime for reproducibility.

The initial package runs one worker with four concurrent trial containers.
Increase capacity only after measuring host CPU/memory and staging throughput.
Worker liveness is its database heartbeat in Operations, not a fake HTTP probe.

## Backups and restore

After an initial successful manual run of `ops/ec2/backup.sh`, install its systemd
service/timer from `ops/ec2/` into `/etc/systemd/system`, reload systemd, and enable
`autobench-backup.timer`. It uploads an hourly PostgreSQL custom-format dump and
task source archive to the private S3 bucket under `backups/<UTC timestamp>/`.
The service must run from the documented checkout location. Monitor failures in
the systemd journal; review S3 retention and costs for current backup objects.
Versioning retains overwritten/deleted object history; it is not an isolated
cross-account backup. The host role has no permission to delete object versions.

Rehearse restoration on an isolated host, without pointing its worker at the
production database. Download a matching backup pair, restore the task directory,
create a fresh PostgreSQL 17 database, and use `pg_restore --no-owner` against it.
Apply migrations, configure the same artifact bucket or an isolated copy, and
start the API before enabling workers. Verify historical scores, a trajectory,
artifact URLs, and a new oracle/mock run. Record measured RPO/RTO and backup age.
Backups do not include the environment file; restore its secrets from the secret
manager. Do not claim recovery acceptance until a real restore drill passes.

## Release acceptance still required

- Test live TLS and Google callbacks, Workspace rejection, session logout and
  expiry, member permissions, scoped CI keys, and recovery administrator access.
- Verify Docker/gVisor execution and image trust, read-only root/writable-workspace
  policy, task build isolation, and Docker API ownership controls. The current
  socket proxy filters endpoint classes; it does not authorize by ownership.
  Enable authoring only for trusted administrators until this review is complete.
- Run Postgres/multi-worker crash recovery, durable SSE reconnect, S3 artifact
  retrieval and an actual backup restore. Scan the public IP to confirm only
  80/443 are reachable.
- Complete the PRD's 50-task corpus, capacity acceptance and 14-day staging SLO
  soak. The repository currently contains four reference tasks.
- Implement project-scoped data isolation/memberships before offering multiple
  independent projects or organizations in one deployment.

Local tests cannot certify these infrastructure and operational gates.

References: [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect),
[EC2 IAM roles](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html),
[Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/),
[gVisor installation](https://gvisor.dev/docs/user_guide/install/),
[EC2 metadata options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-IMDS-new-instances.html).
