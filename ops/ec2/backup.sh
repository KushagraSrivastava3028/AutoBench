#!/usr/bin/env bash
# Run on the host using its IAM role. Produces an atomic PostgreSQL custom dump.
set -euo pipefail
umask 077
cd /srv/autobench/app
compose=(docker compose --env-file ops/ec2/production.env -f docker-compose.ec2.yml)
bucket=$("${compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["api"]["environment"]["AUTOBENCH_S3_BUCKET"])')
stamp=$(date -u +%Y%m%dT%H%M%SZ)
work=$(mktemp -d /var/tmp/autobench-backup.XXXXXXXX)
trap 'rm -f -- "$work/database.dump" "$work/tasks.tar.gz"; rmdir -- "$work"' EXIT
"${compose[@]}" exec -T postgres pg_dump -U autobench -d autobench -Fc > "$work/database.dump"
"${compose[@]}" exec -T postgres pg_restore --list < "$work/database.dump" > /dev/null
tar -czf "$work/tasks.tar.gz" -C /srv/autobench tasks
aws s3 cp "$work/database.dump" "s3://$bucket/backups/$stamp/database.dump" --only-show-errors --sse AES256
aws s3 cp "$work/tasks.tar.gz" "s3://$bucket/backups/$stamp/tasks.tar.gz" --only-show-errors --sse AES256
printf 'AutoBench database and task backup completed: %s\n' "$stamp"
