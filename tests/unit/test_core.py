"""Redaction, seeds, error classification, scoring reports and the rate limiter.

The error-classification tests are the ones to read first: the retry rule is the
single most consequential invariant in the system, and these are what stop it
from quietly eroding.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from autobench.core.errors import (
    AgentTimeout,
    ErrorClass,
    ProviderError,
    ToolCapExceeded,
    TrialError,
    VerifierError,
)
from autobench.core.ids import derive_seed, hash_seed
from autobench.core.security import Scope, generate_api_key, redact, redact_payload, verify_api_key
from autobench.models.pricing import estimate_cost
from autobench.models.ratelimit import InMemoryTokenBucket
from autobench.scoring.report import ScoreItem, ScoreReport

# ---------------------------------------------------------------------------
# the retry rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_class",
    [
        ErrorClass.INFRA_IMAGE,
        ErrorClass.INFRA_DOCKER,
        ErrorClass.INFRA_SETUP,
        ErrorClass.INFRA_SNAPSHOT,
        ErrorClass.INFRA_PERSIST,
        ErrorClass.INFRA_PROVIDER,
        ErrorClass.VERIFIER_CRASH,
    ],
)
def test_infrastructure_failures_are_retried_and_unscored(error_class: ErrorClass) -> None:
    assert error_class.is_infra
    assert error_class.is_retryable
    assert not error_class.counts_as_failure


@pytest.mark.parametrize(
    "error_class",
    [
        ErrorClass.AGENT_TIMEOUT,
        ErrorClass.AGENT_TOOL_CAP,
        ErrorClass.AGENT_TOKEN_CAP,
        ErrorClass.AGENT_ERROR,
        ErrorClass.AGENT_REFUSED,
    ],
)
def test_agent_failures_are_final_and_scored(error_class: ErrorClass) -> None:
    """Re-rolling an agent failure until it passes is how a benchmark inflates."""
    assert not error_class.is_infra
    assert not error_class.is_retryable
    assert error_class.counts_as_failure


def test_abandoned_is_infra_but_not_retryable() -> None:
    assert ErrorClass.INFRA_ABANDONED.is_infra
    assert not ErrorClass.INFRA_ABANDONED.is_retryable


def test_exception_types_carry_the_right_class() -> None:
    assert AgentTimeout("x").error_class is ErrorClass.AGENT_TIMEOUT
    assert ToolCapExceeded("x").error_class is ErrorClass.AGENT_TOOL_CAP
    assert ProviderError("x").error_class is ErrorClass.INFRA_PROVIDER
    assert VerifierError("x").error_class is ErrorClass.VERIFIER_CRASH
    assert not AgentTimeout("x").retryable
    assert ProviderError("x").retryable


def test_problem_json_shape() -> None:
    problem = TrialError("boom", ErrorClass.INFRA_DOCKER).to_problem("/v1/runs/x")
    assert problem["status"] == 500
    assert problem["instance"] == "/v1/runs/x"
    assert problem["detail"] == "boom"


# ---------------------------------------------------------------------------
# seeds
# ---------------------------------------------------------------------------


def test_derive_seed_is_stable_and_attempt_specific() -> None:
    a = derive_seed(42, "11111111-1111-1111-1111-111111111111", 0)
    b = derive_seed(42, "11111111-1111-1111-1111-111111111111", 0)
    c = derive_seed(42, "11111111-1111-1111-1111-111111111111", 1)
    d = derive_seed(43, "11111111-1111-1111-1111-111111111111", 0)
    assert a == b, "a trial must replay identically"
    assert a != c, "two attempts of one task must differ"
    assert a != d, "a different run seed must differ"


def test_hash_seed_fits_cpython_range() -> None:
    """PYTHONHASHSEED outside 0..2**32-1 fatally exits the interpreter."""
    for seed in (0, 1337, 2**62, 2**63 - 1):
        folded = hash_seed(seed)
        assert 0 <= folded <= 4_294_967_295


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        "AIzaSyA1234567890abcdefghijklmnopqrstuvw",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ],
)
def test_known_key_shapes_are_scrubbed(secret: str) -> None:
    assert secret not in redact(f"export KEY={secret} && run")


def test_env_assignments_are_scrubbed() -> None:
    scrubbed = redact("ANTHROPIC_API_KEY=totally-secret-value-here")
    assert "totally-secret-value-here" not in scrubbed
    assert "ANTHROPIC_API_KEY" in scrubbed


def test_auth_headers_are_scrubbed() -> None:
    assert "Bearer" not in redact(
        "Authorization: Bearer abcdefghijklmnop"
    ) or "«redacted»" in redact("Authorization: Bearer abcdefghijklmnop")


def test_hex_digests_survive_redaction() -> None:
    """Container ids and content hashes are exactly what you need when debugging."""
    digest = "sha256:" + "a1b2c3d4" * 8
    assert digest in redact(f"image {digest} built")


def test_redaction_is_recursive_and_idempotent() -> None:
    payload = {
        "command": "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz012345",
        "nested": [{"token": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"}],
    }
    once = redact_payload(payload)
    twice = redact_payload(once)
    assert once == twice
    assert "sk-ant" not in json.dumps(once)
    assert "sk-abcdef" not in json.dumps(once)


# ---------------------------------------------------------------------------
# api keys and scopes
# ---------------------------------------------------------------------------


def test_api_key_round_trip() -> None:
    plaintext, prefix, hashed = generate_api_key()
    assert plaintext.startswith("ab_")
    assert plaintext.startswith(prefix)
    assert verify_api_key(plaintext, hashed)
    assert not verify_api_key("ab_wrong", hashed)


def test_scope_implication() -> None:
    assert Scope.implied_by({"admin"}) == set(Scope)
    assert Scope.implied_by({"author"}) == {Scope.AUTHOR, Scope.RUN, Scope.READ}
    assert Scope.implied_by({"read"}) == {Scope.READ}
    assert Scope.implied_by({"nonsense"}) == set()


# ---------------------------------------------------------------------------
# score report
# ---------------------------------------------------------------------------


def test_weighted_reward_from_items() -> None:
    report = ScoreReport.from_items(
        [
            ScoreItem(id="a", passed=True, weight=0.5),
            ScoreItem(id="b", passed=True, weight=0.3),
            ScoreItem(id="c", passed=False, weight=0.2),
        ]
    )
    assert report.reward == pytest.approx(0.8)
    assert not report.binary_pass


def test_items_win_over_a_self_reported_reward() -> None:
    """A summary that disagrees with its evidence is a bug in the test harness."""
    report = ScoreReport.parse(
        json.dumps(
            {
                "items": [{"id": "a", "passed": False, "weight": 1.0}],
                "reward": 1.0,
            }
        )
    )
    assert report.reward == 0.0
    assert report.error is not None and "items win" in report.error


def test_missing_report_scores_zero_not_none() -> None:
    report = ScoreReport.parse("", exit_code=1)
    assert report.reward == 0.0
    assert not report.binary_pass
    assert report.error


def test_exit_code_is_a_fallback_only() -> None:
    report = ScoreReport.parse(json.dumps({"reward": 0.5}), exit_code=0)
    assert report.reward == 0.5


def test_garbage_report_does_not_become_a_pass() -> None:
    report = ScoreReport.parse("Traceback (most recent call last):", exit_code=1)
    assert report.reward == 0.0


# ---------------------------------------------------------------------------
# pricing and rate limiting
# ---------------------------------------------------------------------------


def test_unknown_model_is_unpriced_not_free() -> None:
    cost, known = estimate_cost("anthropic", "not-a-real-model", 1000, 1000)
    assert cost == 0.0
    assert known is False, "the caller must be able to tell 'unpriced' from '$0.00'"


def test_cached_tokens_are_billed_separately() -> None:
    full, _ = estimate_cost("anthropic", "claude-opus-5", 1_000_000, 0, 0)
    cached, _ = estimate_cost("anthropic", "claude-opus-5", 0, 0, 1_000_000)
    assert cached < full


async def test_in_memory_bucket_enforces_rpm() -> None:
    bucket = InMemoryTokenBucket(rpm=2, tpm=1_000_000, max_concurrent=8)
    for _ in range(2):
        await bucket.acquire("mock", "m", 10)
        await bucket.release("mock", "m", 10)

    # The third acquire must block; we only check that it does not sail through.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bucket.acquire("mock", "m", 10), timeout=0.4)


async def test_in_memory_bucket_releases_concurrency_on_cancel() -> None:
    """A cancelled acquire must not leak a permanently-held slot."""
    bucket = InMemoryTokenBucket(rpm=1, tpm=1_000_000, max_concurrent=1)
    await bucket.acquire("mock", "m", 10)
    await bucket.release("mock", "m", 10)

    task = asyncio.create_task(bucket.acquire("mock", "m", 10))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bucket._concurrency._value == 1
