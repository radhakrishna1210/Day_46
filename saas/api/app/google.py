"""Google sign-in -- OAuth 2.0 authorization-code flow with OpenID Connect.

Registered in Google Cloud Console (OAuth client, "Web application"):
    Authorized JavaScript origin:  <RECOVA_PUBLIC_URL>            e.g. http://localhost:3000
    Authorized redirect URI:       <RECOVA_PUBLIC_URL>/api/auth/google/callback

The redirect goes through the web app's /api proxy, so the session cookie the
API sets on the way back is first-party. Scopes: openid email profile only.
The ID token is verified here -- signature against Google's published keys,
audience = our client id, issuer = Google, and the nonce we sent.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwks: jwt.PyJWKClient | None = None


def redirect_uri() -> str:
    return f"{settings.public_url()}/api/auth/google/callback"


def authorization_url(state: str, nonce: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": settings.env("GOOGLE_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    })


def exchange_code(code: str, nonce: str) -> dict[str, Any]:
    """Swap the authorization code for tokens and return the VERIFIED ID-token
    claims (sub, email, email_verified, name). Raises ValueError on any
    failure -- the caller turns that into a friendly sign-in error."""
    global _jwks
    resp = httpx.post(TOKEN_URL, timeout=15, data={
        "code": code,
        "client_id": settings.env("GOOGLE_CLIENT_ID"),
        "client_secret": settings.env("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    })
    if resp.status_code != 200:
        raise ValueError(f"Google token exchange failed ({resp.status_code})")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise ValueError("Google returned no ID token")
    _jwks = _jwks or jwt.PyJWKClient(JWKS_URL)
    key = _jwks.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(id_token, key, algorithms=["RS256"],
                        audience=settings.env("GOOGLE_CLIENT_ID"), issuer=ISSUERS)
    if claims.get("nonce") != nonce:
        raise ValueError("Google sign-in nonce mismatch")
    return claims
