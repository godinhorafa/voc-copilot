"""An in-memory OpenID provider: signs ID tokens and serves its JWKS to httpx."""

import json
import time
import uuid
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test"
AUDIENCE = "voc-copilot"


class FakeIdP:
    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.requests: list[str] = []
        self.kid = self.rotate()

    def rotate(self) -> str:
        """Add a signing key and use it from now on."""
        kid = uuid.uuid4().hex[:8]
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = kid
        return kid

    def jwks(self) -> dict[str, Any]:
        keys = []
        for kid, key in self.keys.items():
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            keys.append(jwk | {"kid": kid, "use": "sig", "alg": "RS256"})
        return {"keys": keys}

    def token(self, email: str | None = "ana@acme.test", **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": f"sub-{email}",
            "email": email,
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
        }
        kid = overrides.pop("kid", self.kid)
        claims |= overrides
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.keys[kid], algorithm="RS256", headers={"kid": kid})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json=self.jwks())
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
