import asyncio
import logging
import sys
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

# Allow imports from project root when run as __main__
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from gmail_client import GmailClient
from llm_client import LLMClient
from priority1_client import Priority1Client
from api.routes import health, quote, booking, documents

logger = logging.getLogger(__name__)


async def _polling_loop(gmail: GmailClient, llm: LLMClient, p1: Priority1Client):
    """Run the Gmail inbox polling loop as an asyncio background task."""
    # Import here to avoid circular import (main imports from api.app)
    from main import check_inbox

    logger.info(f"Polling loop started — checking every {config.POLL_INTERVAL_MINUTES} minute(s).")
    while True:
        try:
            await asyncio.to_thread(check_inbox, gmail, llm, p1)
        except asyncio.CancelledError:
            logger.info("Polling loop cancelled during shutdown.")
            raise
        except Exception as e:
            logger.error(f"Polling loop error (will retry next interval): {e}", exc_info=True)
        logger.info(f"Inbox check done — next check in {config.POLL_INTERVAL_MINUTES} minute(s). Running continuously.")
        await asyncio.sleep(config.POLL_INTERVAL_MINUTES * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: start polling loop on startup, cancel on shutdown."""
    gmail = GmailClient()
    llm = LLMClient()
    p1 = Priority1Client()

    polling_task = asyncio.create_task(_polling_loop(gmail, llm, p1))
    logger.info("Email2Quote API server started. Gmail polling is active.")

    yield  # Server is running

    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    logger.info("Email2Quote API server stopped.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Email2Quote API",
        description=(
            "## Two-step freight quoting and booking workflow\n\n"
            "### Step 1 — Get carrier quotes\n"
            "Submit a BOL PDF (`POST /quote/bol`) or plain-text description (`POST /quote/text`). "
            "Email2Quote extracts all freight details (weight, dimensions, class, pieces) via LLM "
            "and fetches live rates from Priority1. "
            "Returns `QuoteResponse` with `extracted_details` (parsed BOL fields including "
            "shipper/consignee phones) and `quotes` (one entry per carrier).\n\n"
            "### Step 2 — Book the chosen quote\n"
            "Call `POST /book` with the selected `quote_id`, shipper and consignee addresses, "
            "and a pickup date. **Do not re-send freight details** — Email2Quote retrieves them "
            "from its server-side cache using the `quote_id` and submits the dispatch to "
            "Priority1 with an exact item match. "
            "Returns BOL number, pickup confirmation number, and document URLs.\n\n"
            "### Step 3 — Retrieve documents\n"
            "- `GET /document/{bol_number}` — download the BOL PDF immediately after booking.\n"
            "- `GET /invoice?bol_number={bol_number}` — fetch the freight invoice after delivery "
            "(available 1–3 business days post-delivery).\n\n"
            "**Authentication:** All endpoints require `X-API-Key` header."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(quote.router)
    app.include_router(booking.router)
    app.include_router(documents.router)
    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=config.API_HOST,
        port=config.API_PORT,
        reload=False,
    )
