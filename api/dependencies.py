from fastapi import Header, HTTPException, status
import config


async def verify_api_key(x_api_key: str = Header(..., description="API key for authentication")):
    """Validate X-API-Key header against configured API_KEY."""
    if not config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY is not configured on the server. Set it in .env.",
        )
    if x_api_key != config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
