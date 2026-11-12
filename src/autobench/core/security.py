"""API keys, scopes and secret redaction.

Redaction runs on the write path, not the read path. A secret that reaches
Postgres has already leaked — backups, replicas and ``pg_dump`` all copy it. So
every trajectory event is scrubbed before it is persisted, and the scrubber is
deliberately over-eager: a false positive costs a debugging session, a false
negative costs a provider key.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from collections import Counter
from enum import Enum
from typing import Any

_KEY_PREFIX = "ab"
_KEY_BYTES = 32


class Scope(str, Enum):
    READ = "read"
    RUN = "run"
    AUTHOR = "author"
    ADMIN = "admin"

    @classmethod
    def implied_by(cls, granted: set[str]) -> set[Scope]:
        """``admin`` implies everything; ``author`` implies ``run`` implies ``read``."""
        resolved: set[Scope] = set()
        for raw in granted:
            try:
                scope = cls(raw)
            except ValueError:
                continue
            resolved.add(scope)
        if Scope.ADMIN in resolved:
            return set(cls)
        if Scope.AUTHOR in resolved:
            resolved |= {Scope.RUN, Scope.READ}
        if Scope.RUN in resolved:
            resolved.add(Scope.READ)
        return resolved


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(plaintext, lookup_prefix, hash)``.

    The plaintext is shown once. The prefix is stored in the clear so a lookup
    is an index hit rather than an Argon2 verification against every row.
    """
    raw = secrets.token_urlsafe(_KEY_BYTES)
    plaintext = f"{_KEY_PREFIX}_{raw}"
    return plaintext, key_prefix(plaintext), hash_api_key(plaintext)


def key_prefix(plaintext: str) -> str:
    return plaintext[:12]


def hash_api_key(plaintext: str) -> str:
    """Argon2id when available, salted SHA-256 otherwise.

    The fallback exists so ``pip install autobench`` works without a C toolchain;
    ``Settings.auth_required`` plus a startup warning keep it out of production.
    """
    try:
        from argon2 import PasswordHasher

        return PasswordHasher().hash(plaintext)
    except ImportError:  # pragma: no cover - depends on the install profile
        salt = secrets.token_hex(16)
        digest = hashlib.sha256(f"{salt}:{plaintext}".encode()).hexdigest()
        return f"sha256${salt}${digest}"


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    if stored_hash.startswith("sha256$"):
        _, salt, digest = stored_hash.split("$", 2)
        expected = hashlib.sha256(f"{salt}:{plaintext}".encode()).hexdigest()
        return secrets.compare_digest(expected, digest)
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerificationError, VerifyMismatchError

        try:
            return PasswordHasher().verify(stored_hash, plaintext)
        except (VerifyMismatchError, VerificationError):
            return False
    except ImportError:  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------

REDACTED = "«redacted»"

#: Provider key shapes we know by sight.
_KNOWN_PREFIXES = re.compile(
    r"\b("
    r"sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|sk-proj-[A-Za-z0-9_\-]{16,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_\-]{30,}"
    r"|ghp_[A-Za-z0-9]{30,}"
    r"|gho_[A-Za-z0-9]{30,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|ab_[A-Za-z0-9_\-]{30,}"
    r")\b"
)

#: ``ANTHROPIC_API_KEY=...`` in a shell line, an env dump, a Dockerfile.
_ENV_ASSIGNMENT = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?)"
    r"\s*[=:]\s*"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)

_AUTH_HEADER = re.compile(r"(?i)\b(authorization|x-api-key)\b(\s*[:=]\s*)(\S+)")

#: Anything long, dense and high-entropy. The last line of defence.
_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-=]{32,}")

_ENTROPY_THRESHOLD = 3.6


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def redact(text: str) -> str:
    """Scrub a single string. Idempotent, and safe to run on arbitrary bytes-as-text."""
    if not text:
        return text

    text = _KNOWN_PREFIXES.sub(REDACTED, text)
    text = _ENV_ASSIGNMENT.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)

    def _entropy_sub(match: re.Match[str]) -> str:
        candidate = match.group(0)
        # Hex digests and container ids are high-entropy but not secret, and they
        # are exactly what you need when debugging a trial. Keep them.
        if re.fullmatch(r"[0-9a-f]+", candidate):
            return candidate
        if _shannon_entropy(candidate) >= _ENTROPY_THRESHOLD:
            return REDACTED
        return candidate

    return _CANDIDATE.sub(_entropy_sub, text)


def redact_payload(payload: Any, *, _depth: int = 0) -> Any:
    """Recursively scrub a JSON-shaped payload before it is persisted."""
    if _depth > 12:
        return payload
    if isinstance(payload, str):
        return redact(payload)
    if isinstance(payload, dict):
        return {k: redact_payload(v, _depth=_depth + 1) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact_payload(v, _depth=_depth + 1) for v in payload]
    return payload
