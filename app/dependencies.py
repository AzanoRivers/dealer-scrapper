import hmac

from fastapi import Header, HTTPException, status

from app.config import settings


async def verify_api_key(x_api_key: str = Header(...)) -> str:
    """
    Dependency that validates the X-API-Key header.
    Uses hmac.compare_digest for timing-safe comparison.
    Raises 401 if key is wrong, FastAPI raises 422 automatically if header is absent
    (but we use Header(...) which causes 422; for a clean 401 on missing, we catch it in router).
    """
    if not hmac.compare_digest(x_api_key, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    return x_api_key
