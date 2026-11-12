"""Check a rendered standalone Compose config before exposing the EC2 stack.

docker compose --env-file ops/ec2/production.env -f docker-compose.ec2.yml \
    config --format json | python scripts/validate_ec2_config.py
Does not print secrets, connect to AWS, or start any services.
"""

import json
import sys
from typing import Any


def validate(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    services = config.get("services", {})
    for name, service in services.items():
        ports = service.get("ports", [])
        if name != "caddy" and ports:
            errors.append(f"{name} publishes a host port")
        if name == "caddy" and {str(p["published"]) for p in ports} != {"80", "443"}:
            errors.append("Caddy must publish only ports 80 and 443")
    for name in ("api", "worker", "migrate"):
        env = services.get(name, {}).get("environment", {})
        required = {
            "AUTOBENCH_ENVIRONMENT": "production",
            "AUTOBENCH_AUTH_REQUIRED": "true",
            "AUTOBENCH_EXECUTOR": "docker",
            "AUTOBENCH_QUEUE": "redis",
            "AUTOBENCH_ARTIFACT_BACKEND": "s3",
            "AUTOBENCH_SANDBOX__RUNTIME": "runsc",
            "AUTOBENCH_SANDBOX__REQUIRE_NETWORK_NONE": "true",
        }
        for key, expected in required.items():
            if str(env.get(key)) != expected:
                errors.append(f"{name}: {key} must be {expected}")
        for key in (
            "AUTOBENCH_S3_ACCESS_KEY",
            "AUTOBENCH_S3_SECRET_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AUTOBENCH_S3_ENDPOINT_URL",
        ):
            if env.get(key):
                errors.append(f"{name}: omit {key} to use the EC2 role and native S3")
        if not str(env.get("AUTOBENCH_PUBLIC_URL", "")).startswith("https://"):
            errors.append(f"{name}: public origin requires HTTPS")
        for key in (
            "AUTOBENCH_GOOGLE_OAUTH_CLIENT_ID",
            "AUTOBENCH_GOOGLE_OAUTH_CLIENT_SECRET",
            "AUTOBENCH_GOOGLE_WORKSPACE_DOMAINS",
            "AUTOBENCH_GOOGLE_BOOTSTRAP_ADMINS",
            "AUTOBENCH_DATABASE_URL",
            "AUTOBENCH_REDIS_URL",
            "AUTOBENCH_S3_BUCKET",
        ):
            value = str(env.get(key, ""))
            if not value or "REPLACE" in value or value == "[]":
                errors.append(f"{name}: configure {key}")
    if not services.get("postgres", {}).get("image", "").startswith("postgres:17"):
        errors.append("PostgreSQL 17 is required")
    if not services.get("valkey", {}).get("image", "").startswith("valkey/valkey:8"):
        errors.append("Valkey 8 is required")
    ui_env = services.get("ui", {}).get("environment", {})
    if ui_env.get("AUTOBENCH_AUTH_REQUIRED") != "true":
        errors.append("UI authentication must be required")
    return errors


if __name__ == "__main__":
    try:
        problems = validate(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError):
        sys.exit("Invalid Compose JSON; run docker compose config --format json first.")
    if problems:
        sys.exit("EC2 configuration rejected:\n" + "\n".join(problems))
    print("EC2 configuration checks passed. No services were started.")
