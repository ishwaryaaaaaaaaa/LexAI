"""
auth.py
=======
Server-side verification of Supabase access tokens for the LexAI API.

`get_user_id` is a FastAPI dependency: it reads "Authorization: Bearer <token>",
verifies the token and returns its `sub` claim (the Supabase user id).

  - RS256 / ES256 tokens are verified against the project's public keys at
    {SUPABASE_URL}/auth/v1/.well-known/jwks.json (fetched by PyJWKClient and
    cached in memory, not fetched on every request).
  - HS256 tokens (legacy shared-secret projects) are verified with
    SUPABASE_JWT_SECRET; if that is unset, HS256 tokens are rejected.
  - Any other algorithm, including "none", is rejected.
  - exp, iss ({SUPABASE_URL}/auth/v1), aud ("authenticated") and sub are
    always required.

Every failure is a 401 with the same generic message; token contents are
never logged or returned.

AUTH_DISABLED=true turns verification off for local development only: the
dependency returns None and the API falls back to a client-supplied owner_id.
If SUPABASE_URL is unset and auth is not disabled, check_startup_config()
raises so the server refuses to start instead of running unprotected.
"""

import os
import threading

import jwt
from fastapi import Header, HTTPException

GENERIC_401 = "Invalid or missing credentials."
AUDIENCE = "authenticated"
_ASYMMETRIC_ALGS = {"RS256", "ES256"}


class AuthConfigError(RuntimeError):
    """Raised at startup when auth is enabled but not configured."""


def auth_disabled():
    return os.getenv("AUTH_DISABLED", "false").strip().lower() == "true"


def _supabase_url():
    return os.getenv("SUPABASE_URL", "").strip().rstrip("/")


def check_startup_config():
    """Call once at server start. Refuses to start when auth is enabled but
    SUPABASE_URL is missing; prints a loud warning when auth is disabled."""
    if auth_disabled():
        print("=" * 60)
        print("WARNING: AUTH DISABLED - local development only")
        print("=" * 60)
        return
    if not _supabase_url():
        raise AuthConfigError(
            "SUPABASE_URL is not set. Set SUPABASE_URL to your Supabase project URL "
            "(e.g. https://<project-ref>.supabase.co) so the API can verify access tokens, "
            "or set AUTH_DISABLED=true for local development only."
        )


# One JWKS client per Supabase URL, reused across requests and threads.
# PyJWKClient caches the fetched key set (lifespan 300s) and, with
# cache_keys=True, the resolved signing keys.
_jwks_clients = {}
_jwks_lock = threading.Lock()


def _get_jwks_client(supabase_url):
    with _jwks_lock:
        client = _jwks_clients.get(supabase_url)
        if client is None:
            client = jwt.PyJWKClient(
                f"{supabase_url}/auth/v1/.well-known/jwks.json",
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=300,
                timeout=10,
            )
            _jwks_clients[supabase_url] = client
        return client


def _unauthorized():
    return HTTPException(status_code=401, detail=GENERIC_401,
                         headers={"WWW-Authenticate": "Bearer"})


def verify_token(token):
    """Return the token's `sub` if it is valid; raise HTTPException(401) otherwise."""
    supabase_url = _supabase_url()
    if not supabase_url:
        raise _unauthorized()
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg in _ASYMMETRIC_ALGS:
            key = _get_jwks_client(supabase_url).get_signing_key_from_jwt(token).key
        elif alg == "HS256":
            key = os.getenv("SUPABASE_JWT_SECRET", "")
            if not key:
                raise _unauthorized()
        else:
            raise _unauthorized()
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=AUDIENCE,
            issuer=f"{supabase_url}/auth/v1",
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except HTTPException:
        raise
    except Exception:
        # Any decode/verification/JWKS problem: same generic 401, no details.
        raise _unauthorized()
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise _unauthorized()
    return sub


def get_user_id(authorization: str | None = Header(default=None)):
    """FastAPI dependency: the verified user id, or None when AUTH_DISABLED=true."""
    if auth_disabled():
        return None
    if not authorization:
        raise _unauthorized()
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise _unauthorized()
    return verify_token(parts[1])
