"""Pseudonym ↔ original-value vault.

A pseudonym is a stable, deterministic-per-process token (e.g. ``PT_a1b2c3``).
The vault stores the reversible mapping. Re-identification requires the
``FHIR_MCP_REID_KEY`` (or a SMART scope in v0.2) and is always audited.

The vault uses HMAC-SHA256 over a per-vault salt so that the same original
value always produces the same pseudonym within a single deployment, but
pseudonyms cannot be guessed from the original without the salt.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, ClassVar

import structlog
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

# Pseudonym tail width in hex chars. 16 hex = 64 bits → birthday collision
# space ≈ 2^32 entries before 50% probability — safe for any realistic vault.
_PSEUDONYM_HEX = 16
_PSEUDONYM_MAX_EXTEND = 64  # max width before we admit defeat on a collision

log = structlog.get_logger(__name__)


class _Base(DeclarativeBase):
    pass


class VaultEntry(_Base):
    __tablename__ = "phi_vault"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pseudonym = Column(String(64), nullable=False, unique=True, index=True)
    original = Column(String(1024), nullable=False)  # plaintext at rest; AES-GCM is roadmap item v0.3 (see THREAT_MODEL.md TB-C)
    kind = Column(String(32), nullable=False)  # name, mrn, address, dob, phone, email, ...
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (UniqueConstraint("pseudonym", name="uq_phi_pseudonym"),)


class VaultSalt(_Base):
    __tablename__ = "phi_vault_salt"
    id = Column(Integer, primary_key=True)
    salt = Column(String(64), nullable=False)


_PREFIXES: dict[str, str] = {
    "name": "NM",
    "given": "GN",
    "family": "FM",
    "mrn": "MR",
    "identifier": "ID",
    "ssn": "SS",
    "address": "AD",
    "city": "CT",
    "postal": "ZP",
    "dob": "DB",
    "date": "DT",
    "phone": "PH",
    "email": "EM",
    "patient_ref": "PT",
}


class Vault:
    _instance: ClassVar[Vault | None] = None

    def __init__(self) -> None:
        self._engine: Any | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._salt: bytes | None = None
        self._initialized = False
        self._inproc: dict[str, str] = {}  # pseudonym -> original (fast cache)
        self._reverse: dict[tuple[str, str], str] = {}  # (kind, original) -> pseudonym
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()

    @classmethod
    def instance(cls) -> Vault:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            settings = get_settings()
            self._engine = create_async_engine(settings.vault_url(), future=True)
            self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
            async with self._engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)
            assert self._session_factory is not None
            async with self._session_factory() as session:
                row = (await session.execute(select(VaultSalt).limit(1))).scalar_one_or_none()
                if row is None:
                    salt_hex = secrets.token_hex(16)
                    session.add(VaultSalt(salt=salt_hex))
                    await session.commit()
                    self._salt = bytes.fromhex(salt_hex)
                else:
                    self._salt = bytes.fromhex(str(row.salt))
            self._initialized = True

    def _mint(self, kind: str, original: str, width: int = _PSEUDONYM_HEX) -> str:
        assert self._salt is not None
        prefix = _PREFIXES.get(kind, "TK")
        digest = hmac.new(self._salt, f"{kind}|{original}".encode(), sha256).hexdigest()[:width]
        return f"{prefix}_{digest}"

    async def _mint_collision_free(
        self, kind: str, original: str, session: AsyncSession
    ) -> str:
        """Mint a pseudonym; if the same token already maps to a different original,
        extend the digest width until we resolve the collision. In ~all practical
        cases this returns on the first try because of the 64-bit width."""
        width = _PSEUDONYM_HEX
        while width <= _PSEUDONYM_MAX_EXTEND:
            candidate = self._mint(kind, original, width)
            existing = (
                await session.execute(
                    select(VaultEntry).where(VaultEntry.pseudonym == candidate)
                )
            ).scalar_one_or_none()
            if existing is None or str(existing.original) == original:
                return candidate
            log.warning("vault_collision", pseudonym=candidate, width=width)
            width += 4
        raise RuntimeError(f"vault: irresolvable collision for kind={kind}")

    async def pseudonymize(self, kind: str, original: str) -> str:
        if not original:
            return original
        await self.init()
        key = (kind, original)
        async with self._lock:
            if key in self._reverse:
                return self._reverse[key]
            assert self._session_factory is not None
            try:
                async with self._session_factory() as session:
                    pseudonym = await self._mint_collision_free(kind, original, session)
                    existing = (
                        await session.execute(
                            select(VaultEntry).where(VaultEntry.pseudonym == pseudonym)
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(
                            VaultEntry(pseudonym=pseudonym, original=original, kind=kind)
                        )
                        await session.commit()
            except Exception as exc:  # pragma: no cover
                log.error("vault_persist_failed", error=str(exc))
                pseudonym = self._mint(kind, original)
            self._reverse[key] = pseudonym
            self._inproc[pseudonym] = original
            return pseudonym

    async def lookup(self, pseudonym: str) -> str | None:
        if pseudonym in self._inproc:
            return self._inproc[pseudonym]
        await self.init()
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(VaultEntry).where(VaultEntry.pseudonym == pseudonym)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            original = str(row.original)
            self._inproc[pseudonym] = original
            return original


def get_vault() -> Vault:
    return Vault.instance()


def reset_vault_for_tests() -> None:
    Vault._instance = None
