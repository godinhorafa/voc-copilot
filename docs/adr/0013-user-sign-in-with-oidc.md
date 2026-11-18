# 0013. User sign-in with OpenID Connect at the proxy

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

The web UI reached the API through nginx, which added one shared API key. Everyone who could open the page acted as the default tenant's admin. Since ADR 0012 the API can scope requests to a tenant and a role, so we need to know which person is behind each request. Organizations already have an identity provider (Microsoft Entra ID, Google, Okta, Keycloak). They expect single sign-on, not another password.

## Decision

**oauth2-proxy signs people in. The API verifies their ID token and decides access.**

- **Request path:** the browser talks to oauth2-proxy, which runs the OpenID Connect authorization code flow with PKCE against the provider. It keeps the session in an encrypted cookie and forwards every request to the web nginx with `Authorization: Bearer <ID token>`. nginx passes the header on to the API.
- **Verification in the API** (`app/core/oidc.py`). We don't trust whatever the proxy says; the API checks the token itself:
  - **Signature:** checked against the provider's JWKS, found through discovery or set with `OIDC_JWKS_URL`. Only asymmetric algorithms are accepted, so `none` and `HS256` with public key material are refused.
  - **Claims:** issuer, audience (our client id) and expiry are checked, with 30 s of leeway.
  - **Email:** the email claim (configurable for Entra ID) names the user. An email the provider marks unverified is refused.
  - **Key rotation:** keys are cached. An unknown key id triggers at most one refetch per minute.
- **Access is a list in our database** (`users`: email → tenant + role, one tenant per email). Signing in proves identity; it does not grant access.
  - **No match:** a verified token whose email is not on the list gets `403`, and the UI shows "You don't have access yet".
  - **Removal:** deleting a user takes effect on their next request, even if their token is still valid.
  - **Who manages it:** tenant admins use `/v1/users` (add, change role, remove; not themselves). Operators use `scripts/tenants.py add-user`.
- **One credential per request.** When a bearer token is present, it is the only credential considered. An invalid token gets `401`; the API never falls back to an `X-API-Key` that a proxy might add. In SSO mode the web container adds no key and is not published, so the only way in is through sign-in. API keys remain for machine clients (ingestion jobs, scripts).
- **The UI** reads `/v1/me` and shows:
  - who is signed in, with tenant and role, and a sign-out link;
  - "not available for your role" instead of failing calls;
  - a no-access screen and a session-expired screen.
- **Local setup.** `docker-compose.sso.yml` adds oauth2-proxy and Dex as a stand-in provider with four test users (`make up-sso`, `make demo-sso`). Production swaps Dex for the real provider through environment variables.

## Alternatives considered

- **Login in the FastAPI app** (Authlib, our own sessions and callback). This puts cookies, CSRF, refresh and sign-out logic in our code. oauth2-proxy is widely used and maintained, and works with any OIDC provider.
- **Trusting `X-Forwarded-Email` from the proxy.** It is simpler: no token verification in the API. But anything that can reach the API port could then impersonate anyone. Verifying a signed token holds even if the network boundary doesn't.
- **Access tokens instead of ID tokens.** Access token formats vary by provider (Entra ID's are for Microsoft Graph unless you register an API scope). The ID token is standard and its audience is our client id. Moving to access tokens is the right step when other services call the API on a user's behalf.
- **Mapping groups or roles from the provider's claims.** This is useful in large organizations, but claim names and formats differ by provider and need admin consent. An explicit list in our database works everywhere and is auditable. Group mapping can be added on top.
- **An SPA with PKCE in the browser** (tokens in JavaScript). That exposes tokens to XSS. With the proxy, the browser only holds an HttpOnly cookie.

## Consequences

- One email maps to one tenant. A consultant working for two tenants needs two identities. A membership table would lift this when needed.
- Each request does one JWT verification (a cached key and a signature check) and one indexed user lookup.
- Rate limits key on the credential header. A refreshed token starts a new window. That is acceptable for a limit that protects spend, and it needs no lookup before authentication.
- **Unverified pieces.** The compose wiring itself (oauth2-proxy settings) was not run in CI. What was checked end to end against a real Dex:
  - authorization code login, token exchange, nginx forwarding and API verification;
  - roles and the UI screens;
  - a tampered token is rejected.
- **Unit tests** use an in-memory provider:
  - expired, wrong audience or issuer, missing subject or email, unverified email;
  - tampered tokens, a foreign key, `none` and `HS256`;
  - key rotation with throttled refetch, discovery, and an unreachable provider.
- **Integration tests** cover tenant mapping and isolation, no fallback to the API key, user management and the self-lockout guard, and the CLI.
