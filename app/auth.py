from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()


def require_token(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> None:
    if not AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_TOKEN is not configured on the server",
        )

    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif x_api_key:
        provided = x_api_key.strip()

    if not provided or not hmac.compare_digest(provided, AUTH_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
