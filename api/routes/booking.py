import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_api_key
from api.models import BookingRequest, BookingResponse
from priority1_client import Priority1Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/book", tags=["Booking"])

_p1 = Priority1Client()


@router.post(
    "",
    response_model=BookingResponse,
    summary="Book a shipment using a selected carrier quote",
    dependencies=[Depends(verify_api_key)],
)
async def book_shipment(req: BookingRequest):
    """
    Book a shipment on Priority1 using a quote ID returned by `/quote/bol` or `/quote/text`.

    Provide shipper and consignee contact details and a pickup window.
    The freight line-item details are retrieved from the server-side quote cache —
    no need to re-submit them. Priority1 will confirm the booking and return the
    BOL number, pickup confirmation number, and URLs to download the BOL PDF and
    pallet labels.

    **Header required:** `X-API-Key: <your-key>`
    """
    request_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info(f"[{request_id}] NEW BOOKING REQUEST FROM ODOO TMS")
    logger.info(
        f"[{request_id}] Quote ID: {req.quote_id}  |  "
        f"{req.shipper.zip} → {req.consignee.zip}  |  "
        f"Pickup: {req.pickup_date}"
    )
    logger.info("=" * 60)

    result = await asyncio.to_thread(_p1.dispatch_shipment, req)

    if result.get("status") == "error":
        errors = result.get("errors", [])
        logger.error(f"[{request_id}] Booking failed: {errors}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Priority1 dispatch failed: {'; '.join(errors)}",
        )

    logger.info(
        f"[{request_id}] Booking confirmed — "
        f"Shipment ID: {result.get('shipment_id')}  "
        f"BOL: {result.get('bol_number')}  "
        f"Pickup #: {result.get('pickup_number')}"
    )

    return BookingResponse(
        status=result["status"],
        shipment_id=result.get("shipment_id"),
        bol_number=result.get("bol_number"),
        pickup_number=result.get("pickup_number"),
        bol_url=result.get("bol_url"),
        pallet_label_url=result.get("pallet_label_url"),
        estimated_delivery=result.get("estimated_delivery"),
        errors=result.get("errors", []),
        processing_notes=result.get("processing_notes", []),
    )
