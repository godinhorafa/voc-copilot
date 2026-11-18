"""Verify OpenID Connect ID tokens (ADR 0013).

The identity provider signs ID tokens; its public keys are published as a
JWKS. A token is accepted only if its signature checks out against one of
those keys and its issuer, audience (our client id) and expiry are right.
The email claim then names the user; an explicitly unverified email is
refused, because anyone can register an unverified address at most
providers.

Keys are cached. A token signed with a key id we don't know triggers one
refetch (providers rotate keys), at most once per `refresh_interval_s`, so
tokens with made-up key ids can't make us hammer the provider.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

# Asymmetric only: with HS256 the "key" would be a public value anyone could sign with.
ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)


class OIDCError(Exception):
    """The token is not acceptable; the message is safe to return to the client."""


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str  # lowercased


class OIDCVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        email_claim: str = "email",
        leeway_s: int = 30,
        refresh_interval_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self._jwks_url = jwks_url
        self._email_claim = email_claim
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._leeway = leeway_s
        self._refresh_interval = refresh_interval_s
        self._clock = clock
        self._keys: dict[str | None, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str) -> Any:
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("the identity provider's keys could not be loaded") from exc

    async def _refresh(self) -> None:
        if self._jwks_url is None:
            discovery = await self._get_json(
                f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"
            )
            self._jwks_url = str(discovery["jwks_uri"])
        keyset = jwt.PyJWKSet.from_dict(await self._get_json(self._jwks_url))
        self._keys = {key.key_id: key for key in keyset.keys if key.public_key_use in (None, "sig")}
        self._fetched_at = self._clock()

    async def _key(self, kid: str | None) -> jwt.PyJWK | None:
        async with self._lock:
            stale = (
                self._fetched_at is None
                or self._clock() - self._fetched_at >= self._refresh_interval
            )
            if kid not in self._keys and stale:
                await self._refresh()
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return self._keys.get(kid)

    async def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCError("malformed token") from exc
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS:
            raise OIDCError("unsupported token algorithm")
        key = await self._key(header.get("kid"))
        if key is None:
            raise OIDCError("token signed with an unknown key")
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise OIDCError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise OIDCError(f"invalid token: {exc}") from exc

        email = claims.get(self._email_claim)
        if not isinstance(email, str) or "@" not in email:
            raise OIDCError(f"token has no email in its {self._email_claim!r} claim")
        if claims.get("email_verified") is False:
            raise OIDCError("email address not verified by the identity provider")
        return Identity(subject=str(claims["sub"]), email=email.strip().lower())
