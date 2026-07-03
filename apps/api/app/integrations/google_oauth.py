from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

REQUEST_TIMEOUT_SECONDS = 30
AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
SCOPE = "openid email profile"


class GoogleOAuthError(Exception):
    pass


def build_authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


async def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Exchanges an OAuth authorization code for tokens, returning the raw
    token response (its `id_token` is what verify_id_token consumes)."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleOAuthError(f"Could not reach Google's token endpoint: {exc}") from exc

    if response.status_code != 200:
        raise GoogleOAuthError(
            f"Google token exchange returned HTTP {response.status_code}: {response.text[:300]}"
        )
    data = response.json()
    if not data.get("id_token"):
        raise GoogleOAuthError("Google token exchange succeeded but returned no id_token.")
    return data


def verify_id_token(id_token: str, client_id: str) -> dict:
    """Verifies a Google-issued OIDC id_token's signature (via Google's
    published JWKS) and standard claims, returning the decoded payload
    (sub, email, email_verified, hd, name, picture, ...)."""
    try:
        jwks_client = PyJWKClient(JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
        )
    except jwt.PyJWTError as exc:
        raise GoogleOAuthError(f"Google id_token failed verification: {exc}") from exc

    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleOAuthError(f"Unexpected id_token issuer: {claims.get('iss')!r}")
    return claims
