"""Origin checks for authentication operations using browser cookies."""
import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site authentication is not allowed.")
    if origin is None:
        return  # Non-browser clients do not carry an Origin header.
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid authentication origin.") from None
    same_origin = origin == f"{request.url.scheme}://{request.headers.get('host', '')}"
    allowed = os.environ.get(
        "INSPRO_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if parsed.scheme not in {"http", "https"} or not (
        same_origin or origin in {value.strip() for value in allowed}
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site authentication is not allowed.")
