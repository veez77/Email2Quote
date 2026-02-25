import time
from fastapi import APIRouter

router = APIRouter(tags=["Ops"])
_start_time = time.time()


@router.get("/health", summary="Health check")
async def health():
    """Returns server status and uptime. No authentication required."""
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _start_time),
    }
