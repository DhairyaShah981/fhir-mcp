"""Unit tests for SMART OAuth2 (PKCE auth-code + backend-services JWT bearer)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
import pytest

from fhir_mcp.auth.smart import (
    AuthorizationCodeFlow,
    SmartConfig,
    SmartToken,
    _build_client_assertion,
    _pkce_pair,
    asymmetric_client_credentials,
    discover,
)

# --- helpers ---------------------------------------------------------------


class _MockResp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


def _patch_httpx(
    monkeypatch, *, get: _MockResp | None = None, post: _MockResp | None = None
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return None

        async def get(self, url, headers=None):
            calls.append({"method": "GET", "url": url, "headers": headers})
            assert get is not None, "unexpected GET"
            return get

        async def post(self, url, data=None, headers=None):
            calls.append({"method": "POST", "url": url, "data": data, "headers": headers})
            assert post is not None, "unexpected POST"
            return post

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


# --- discovery -------------------------------------------------------------


async def test_discover_parses_well_known(monkeypatch) -> None:
    body = {
        "authorization_endpoint": "https://issuer.example/auth",
        "token_endpoint": "https://issuer.example/token",
        "capabilities": ["launch-standalone"],
        "scopes_supported": ["openid", "patient/*.read"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["private_key_jwt"],
    }
    calls = _patch_httpx(monkeypatch, get=_MockResp(body))
    cfg = await discover("https://issuer.example/fhir")
    assert cfg.token_endpoint == "https://issuer.example/token"
    assert "launch-standalone" in cfg.capabilities
    assert calls[0]["url"].endswith("/.well-known/smart-configuration")


# --- PKCE auth-code flow ---------------------------------------------------


def _config() -> SmartConfig:
    return SmartConfig(
        issuer="https://issuer.example/fhir",
        authorization_endpoint="https://issuer.example/auth",
        token_endpoint="https://issuer.example/token",
    )


def test_pkce_pair_is_random_and_valid() -> None:
    a, b = _pkce_pair()
    assert a != b
    assert len(a) >= 43  # spec minimum
    # Challenge is base64url of sha256(verifier) — no padding chars.
    assert "=" not in b


def test_authorize_url_includes_required_params() -> None:
    flow = AuthorizationCodeFlow(
        config=_config(),
        client_id="cid",
        redirect_uri="http://localhost:8765/cb",
        scopes="patient/*.read",
    )
    url = flow.authorize_url()
    for piece in ("response_type=code", "client_id=cid", "code_challenge=", "code_challenge_method=S256"):
        assert piece in url


async def test_exchange_rejects_state_mismatch() -> None:
    flow = AuthorizationCodeFlow(
        config=_config(), client_id="cid", redirect_uri="http://x", scopes="patient/*.read"
    )
    with pytest.raises(ValueError, match="state mismatch"):
        await flow.exchange("the-code", "WRONG-STATE")


async def test_exchange_success_returns_smart_token(monkeypatch) -> None:
    flow = AuthorizationCodeFlow(
        config=_config(), client_id="cid", redirect_uri="http://x", scopes="patient/*.read"
    )
    body = {
        "access_token": "tok-abc",
        "token_type": "Bearer",
        "expires_in": 600,
        "refresh_token": "rt",
        "scope": "patient/*.read fhir-mcp/reid",
        "patient": "Patient/123",
    }
    _patch_httpx(monkeypatch, post=_MockResp(body))
    token = await flow.exchange("the-code", flow.state)
    assert token.access_token == "tok-abc"
    assert token.refresh_token == "rt"
    assert token.scopes.can_reidentify()
    assert token.patient == "Patient/123"
    assert not token.expired


# --- backend services flow -------------------------------------------------


_RSA_PRIVATE = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDXk6cAvJjI5szP
EIfqIYO29hPo5OWDcU1Ye+VLB7s7uVOWg6vAkqHA4VFKZ3HFQiE+kfqfWyN+gxs9
86mnAxJxTNMcZ5gZb3oG/lQNuKBgRBuFp+gKnYn9w/y0XAg25rZ4VANYJh1c3yyU
S2sg5bM/sm3CD/4w43z+IIyzZ4Re8fJh76Sqes3UnB7q5R1lUgg4Aiyjct2plBJh
4hM3p/G98vGzgRy7tFW9KvyPSofkR1k8Igh9DLAOPjF3qDgvWA/QHm3yX1OK4N18
QNJOPp+ww0Pp/sQ2gPGRsX1jjGZQbSjUaCqr8Cl1g0Y1H1mGm14NQbU40jw6XV2y
GpaHNV41AgMBAAECggEAEZl5C0Cy91Y4N7m9C2OjSqCRsuwxN0HCnNuFp5o7AHCe
6sNH47DZjLwY+yfHRRA2zfWfQ5LMpxQXSPThNNeFXMHbHRKpiP4mPxhKtNyMSm5o
PQQJYg3Kqthxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
-----END PRIVATE KEY-----
"""


def test_build_client_assertion_is_decodable() -> None:
    """Sign a JWT with an in-test ephemeral key and verify it decodes."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    token = _build_client_assertion(
        client_id="cid",
        token_endpoint="https://issuer.example/token",
        private_key_pem=pem,
        key_id="kid-1",
        algorithm="RS256",
        ttl_seconds=120,
    )
    headers = jwt.get_unverified_header(token)
    assert headers["kid"] == "kid-1"
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    decoded = jwt.decode(
        token,
        pub_pem,
        algorithms=["RS256"],
        audience="https://issuer.example/token",
        options={"verify_aud": True, "verify_exp": True},
    )
    assert decoded["iss"] == "cid"
    assert decoded["sub"] == "cid"


async def test_asymmetric_client_credentials_posts_assertion(monkeypatch) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    body = {
        "access_token": "svc-tok",
        "token_type": "Bearer",
        "expires_in": 300,
        "scope": "system/*.read",
    }
    calls = _patch_httpx(monkeypatch, post=_MockResp(body))
    token = await asymmetric_client_credentials(
        _config(),
        client_id="my-svc",
        private_key_pem=pem,
        key_id="kid-svc",
        scopes="system/*.read",
        algorithm="RS256",
    )
    assert token.access_token == "svc-tok"
    assert calls[0]["data"]["grant_type"] == "client_credentials"
    assert calls[0]["data"]["client_assertion_type"].endswith("jwt-bearer")


# --- SmartToken ------------------------------------------------------------


def test_smart_token_expiry() -> None:
    fresh = SmartToken.from_token_response(
        {"access_token": "a", "token_type": "Bearer", "expires_in": 3600, "scope": ""}
    )
    assert not fresh.expired

    from fhir_mcp.auth.scopes import parse_scopes

    stale = SmartToken(
        access_token="x",
        token_type="Bearer",
        expires_at=time.time() - 1,
        refresh_token=None,
        scopes=parse_scopes(""),
    )
    assert stale.expired
