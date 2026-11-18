import time
from typing import Any

import httpx
import jwt
import pytest

from app.core.oidc import OIDCError, OIDCVerifier
from tests.idp import AUDIENCE, ISSUER, FakeIdP


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def verifier(idp: FakeIdP, clock: Clock) -> OIDCVerifier:
    return OIDCVerifier(ISSUER, AUDIENCE, client=idp.client(), clock=clock)


async def test_valid_token(idp: FakeIdP, verifier: OIDCVerifier) -> None:
    identity = await verifier.verify(idp.token("Ana@Acme.TEST"))
    assert identity.email == "ana@acme.test"
    assert identity.subject == "sub-Ana@Acme.TEST"
    # Keys came from the discovery document's jwks_uri, and are cached.
    await verifier.verify(idp.token())
    assert idp.requests == ["/.well-known/openid-configuration", "/keys"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"exp": int(time.time()) - 120}, "token expired"),
        ({"aud": "someone-else"}, "invalid token"),
        ({"iss": "https://evil.test"}, "invalid token"),
        ({"sub": None}, "invalid token"),
        ({"email": None}, "no email in its .email. claim"),
        ({"email_verified": False}, "not verified"),
    ],
)
async def test_rejected_claims(
    idp: FakeIdP, verifier: OIDCVerifier, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(OIDCError, match=message):
        await verifier.verify(idp.token(**overrides))


async def test_rejects_tampered_and_foreign_tokens(idp: FakeIdP, verifier: OIDCVerifier) -> None:
    header, _, signature = idp.token().split(".")
    other = idp.token("mallory@acme.test").split(".")[1]
    with pytest.raises(OIDCError, match="invalid token"):
        await verifier.verify(f"{header}.{other}.{signature}")

    stranger = FakeIdP()  # a different provider's key with our kid
    stranger.keys = {idp.kid: stranger.keys[stranger.kid]}
    stranger.kid = idp.kid
    with pytest.raises(OIDCError, match="invalid token"):
        await verifier.verify(stranger.token())

    with pytest.raises(OIDCError, match="malformed"):
        await verifier.verify("not-a-jwt")


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
async def test_rejects_symmetric_and_unsigned_tokens(
    idp: FakeIdP, verifier: OIDCVerifier, algorithm: str
) -> None:
    claims = jwt.decode(idp.token(), options={"verify_signature": False})
    key = "public-jwks-material-anyone-can-read" if algorithm == "HS256" else ""
    forged = jwt.encode(claims, key, algorithm=algorithm, headers={"kid": idp.kid})
    with pytest.raises(OIDCError, match="unsupported token algorithm"):
        await verifier.verify(forged)


async def test_refetches_keys_after_rotation_but_not_too_often(
    idp: FakeIdP, verifier: OIDCVerifier, clock: Clock
) -> None:
    await verifier.verify(idp.token())
    clock.now += 61
    idp.rotate()
    await verifier.verify(idp.token())  # unknown kid: one refetch finds it
    assert idp.requests.count("/keys") == 2

    with pytest.raises(OIDCError, match="unknown key"):
        await verifier.verify(idp.token(kid=idp.rotate()))  # too soon to refetch again
    assert idp.requests.count("/keys") == 2


async def test_email_from_another_claim(idp: FakeIdP) -> None:
    verifier = OIDCVerifier(ISSUER, AUDIENCE, email_claim="preferred_username", client=idp.client())
    token = idp.token(email=None, preferred_username="Ana@Contoso.test")
    assert (await verifier.verify(token)).email == "ana@contoso.test"
    with pytest.raises(OIDCError, match="'preferred_username'"):
        await verifier.verify(idp.token())


async def test_explicit_jwks_url_skips_discovery(idp: FakeIdP) -> None:
    verifier = OIDCVerifier(ISSUER, AUDIENCE, jwks_url=f"{ISSUER}/keys", client=idp.client())
    await verifier.verify(idp.token())
    assert idp.requests == ["/keys"]


async def test_unreachable_provider() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    verifier = OIDCVerifier(ISSUER, AUDIENCE, client=client)
    with pytest.raises(OIDCError, match="could not be loaded"):
        await verifier.verify(FakeIdP().token())
