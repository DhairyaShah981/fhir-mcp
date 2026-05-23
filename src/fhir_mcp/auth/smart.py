"""SMART on FHIR (v2) client.

Two flows are supported:

* **Authorization code + PKCE** — for interactive dev-sandbox launches
  (SMART Health IT / Epic on FHIR sandbox). The browser dance is the user's
  problem; we own URL construction, code exchange, and token parsing.
* **Asymmetric client credentials (backend services)** — RFC 7523 JWT bearer
  client authentication. The MCP server signs a JWT with its private key and
  posts it to the token endpoint to receive a short-lived access token.

Both flows return a ``SmartToken`` carrying the access token, refresh token
(if any), scopes, and the time of expiry.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import structlog

from .scopes import ScopeSet, parse_scopes

log = structlog.get_logger(__name__)


# --- discovery --------------------------------------------------------------


@dataclass(frozen=True)
class SmartConfig:
    """Parsed .well-known/smart-configuration document."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    capabilities: tuple[str, ...] = ()
    scopes_supported: tuple[str, ...] = ()
    grant_types_supported: tuple[str, ...] = ()
    token_endpoint_auth_methods_supported: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, iss: str, data: dict[str, Any]) -> SmartConfig:
        return cls(
            issuer=iss,
            authorization_endpoint=str(data["authorization_endpoint"]),
            token_endpoint=str(data["token_endpoint"]),
            capabilities=tuple(data.get("capabilities", []) or []),
            scopes_supported=tuple(data.get("scopes_supported", []) or []),
            grant_types_supported=tuple(data.get("grant_types_supported", []) or []),
            token_endpoint_auth_methods_supported=tuple(
                data.get("token_endpoint_auth_methods_supported", []) or []
            ),
        )


async def discover(issuer_url: str, *, timeout: float = 10.0) -> SmartConfig:
    """Fetch ``<issuer>/.well-known/smart-configuration``."""
    iss = issuer_url.rstrip("/")
    url = f"{iss}/.well-known/smart-configuration"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
    return SmartConfig.from_dict(iss, resp.json())


# --- token shape ------------------------------------------------------------


@dataclass(frozen=True)
class SmartToken:
    access_token: str
    token_type: str
    expires_at: float  # epoch seconds
    refresh_token: str | None
    scopes: ScopeSet
    patient: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 5  # 5s clock skew

    @classmethod
    def from_token_response(cls, data: dict[str, Any]) -> SmartToken:
        expires_in = int(data.get("expires_in", 3600))
        return cls(
            access_token=str(data["access_token"]),
            token_type=str(data.get("token_type", "Bearer")),
            expires_at=time.time() + expires_in,
            refresh_token=data.get("refresh_token"),
            scopes=parse_scopes(str(data.get("scope", ""))),
            patient=data.get("patient"),
            raw=data,
        )


# --- PKCE auth-code flow ----------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge_S256)``."""
    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


@dataclass
class AuthorizationCodeFlow:
    """Stateful PKCE auth-code flow holder.

    Typical use:

        flow = AuthorizationCodeFlow(config, client_id, redirect_uri,
                                     scopes="patient/*.read fhir-mcp/reid")
        url = flow.authorize_url()
        # ... user signs in, redirected back with ?code=...&state=...
        token = await flow.exchange(code, state_from_callback)
    """

    config: SmartConfig
    client_id: str
    redirect_uri: str
    scopes: str
    aud: str | None = None
    _state: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    _pkce_verifier: str = ""
    _pkce_challenge: str = ""

    def __post_init__(self) -> None:
        if not self._pkce_verifier:
            self._pkce_verifier, self._pkce_challenge = _pkce_pair()

    @property
    def state(self) -> str:
        return self._state

    def authorize_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": self._state,
            "aud": self.aud or self.config.issuer,
            "code_challenge": self._pkce_challenge,
            "code_challenge_method": "S256",
        }
        return self.config.authorization_endpoint + "?" + urllib.parse.urlencode(params)

    async def exchange(
        self, code: str, callback_state: str, *, timeout: float = 10.0
    ) -> SmartToken:
        if callback_state != self._state:
            raise ValueError("SMART state mismatch — refusing to exchange code.")
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": self._pkce_verifier,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                self.config.token_endpoint,
                data=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            resp.raise_for_status()
        return SmartToken.from_token_response(resp.json())


# --- asymmetric backend-services flow --------------------------------------


def _build_client_assertion(
    *,
    client_id: str,
    token_endpoint: str,
    private_key_pem: str,
    key_id: str,
    algorithm: str = "RS384",
    ttl_seconds: int = 300,
) -> str:
    """Sign a SMART backend-services JWT assertion."""
    now = int(time.time())
    payload = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_endpoint,
        "jti": secrets.token_urlsafe(16),
        "exp": now + ttl_seconds,
        "iat": now,
    }
    headers = {"kid": key_id, "typ": "JWT", "alg": algorithm}
    return jwt.encode(payload, private_key_pem, algorithm=algorithm, headers=headers)


async def asymmetric_client_credentials(
    config: SmartConfig,
    *,
    client_id: str,
    private_key_pem: str,
    key_id: str,
    scopes: str = "system/*.read",
    algorithm: str = "RS384",
    timeout: float = 10.0,
) -> SmartToken:
    """Exchange a signed JWT for an access token (SMART backend services flow)."""
    assertion = _build_client_assertion(
        client_id=client_id,
        token_endpoint=config.token_endpoint,
        private_key_pem=private_key_pem,
        key_id=key_id,
        algorithm=algorithm,
    )
    payload = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": scopes,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            config.token_endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
    return SmartToken.from_token_response(resp.json())
