"""Identifiers, clocks and seed derivation.

Seeds matter more than they look. A trial is a pure function of
``(task version, agent version, model version, seed)``, so the seed cannot be
random per trial — it has to be *derived* from the run seed and the attempt
index. That way re-running a run reproduces the same attempt sequence, and two
attempts of the same task still differ from one another.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

_MAX_SIGNED_64 = (1 << 63) - 1


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    """Timezone-aware UTC. Naive datetimes are a bug waiting on a DST boundary."""
    return datetime.now(UTC)


def short_id(value: uuid.UUID | str, length: int = 8) -> str:
    """The form the UI shows: ``9f2c8a1e``."""
    text = value.hex if isinstance(value, uuid.UUID) else str(value).replace("-", "")
    return text[:length]


def derive_seed(run_seed: int, task_version_id: uuid.UUID | str, attempt_index: int) -> int:
    """Deterministic per-trial seed.

    Stable across processes and Python versions (``hash()`` is not — ``PYTHONHASHSEED``
    randomises it), so this uses BLAKE2b rather than the builtin.
    """
    payload = f"{run_seed}:{task_version_id}:{attempt_index}".encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big") & _MAX_SIGNED_64


def hash_seed(seed: int) -> int:
    """Fold a trial seed into the range CPython accepts for ``PYTHONHASHSEED``.

    Trial seeds are 63-bit so that two attempts of the same task never collide.
    ``PYTHONHASHSEED`` accepts 0..2**32-1 and *fatally* exits the interpreter
    otherwise — which presents as "solve.sh exited 1" with no obvious cause.
    Folding is deterministic, so the reproducibility guarantee is unaffected.
    """
    return seed % 4_294_967_296


def content_digest(*parts: str | bytes) -> str:
    """``sha256:...`` over an ordered list of parts, length-prefixed.

    Length-prefixing stops ``("ab", "c")`` and ``("a", "bc")`` colliding, which
    matters because task hashes are built from path/content pairs.
    """
    hasher = hashlib.sha256()
    for part in parts:
        raw = part.encode("utf-8") if isinstance(part, str) else part
        hasher.update(len(raw).to_bytes(8, "big"))
        hasher.update(raw)
    return f"sha256:{hasher.hexdigest()}"
