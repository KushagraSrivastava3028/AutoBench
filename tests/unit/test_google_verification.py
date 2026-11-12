"""Exercise the official verifier using real RSA signatures and local certificates."""

import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from autobench.api.routers.auth import verify_google_id_token
from autobench.core.config import Settings


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.fixture
def signed_token(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    def certificate_request(*args, **kwargs):
        assert kwargs["timeout"] == 10
        return SimpleNamespace(status=200, data=json.dumps({"test-key": public}).encode())

    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: certificate_request)

    def encode(**overrides):
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "test-client",
            "sub": "123",
            "iat": now,
            "exp": now + 600,
            **overrides,
        }
        payload = (
            b64(json.dumps({"alg": "RS256", "kid": "test-key"}).encode())
            + "."
            + b64(json.dumps(claims).encode())
        )
        signature = key.sign(payload.encode(), padding.PKCS1v15(), hashes.SHA256())
        return payload + "." + b64(signature)

    return encode


async def test_valid_google_signature(signed_token):
    claims = await verify_google_id_token(
        signed_token(), Settings(google_oauth_client_id="test-client")
    )
    assert claims["sub"] == "123"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-client"},
        {"iss": "https://attacker.example"},
        {"iat": 1, "exp": 2},
    ],
)
async def test_wrong_audience_issuer_or_expiry_rejected(signed_token, overrides):
    with pytest.raises(ValueError):
        await verify_google_id_token(
            signed_token(**overrides), Settings(google_oauth_client_id="test-client")
        )


async def test_tampered_signature_rejected(signed_token):
    token = signed_token()
    parts = token.split(".")
    signature = base64.urlsafe_b64decode(parts[2] + "==")
    parts[2] = b64(bytes([signature[0] ^ 1]) + signature[1:])
    with pytest.raises(ValueError):
        await verify_google_id_token(
            ".".join(parts), Settings(google_oauth_client_id="test-client")
        )
