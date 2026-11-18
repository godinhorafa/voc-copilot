"""API key primitives.

Keys look like `voc_<prefix>_<secret>`. Only the SHA-256 of the whole key is
stored: the secret part is 32 random bytes, so a slow password hash would add
latency to every request without adding security (ADR 0012).
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from app.db.models import Role

KEY_PREFIX = "voc"


@dataclass(frozen=True)
class Principal:
    """The caller: which tenant's data it may touch, and what it may do."""

    tenant_id: uuid.UUID
    role: Role
    key_id: uuid.UUID | None = None  # API keys; None for the bootstrap key
    user_id: uuid.UUID | None = None  # people signed in through OIDC
    email: str | None = None

    @property
    def method(self) -> str:
        return "oidc" if self.user_id else "api_key"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> tuple[str, str]:
    """A new key and its public prefix."""
    prefix = secrets.token_hex(4)
    return f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}", prefix
