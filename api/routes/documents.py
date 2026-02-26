import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from api.dependencies import verify_api_key
from api.models import InvoiceResponse
from priority1_client import Priority1Client

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Documents"])

_p1 = Priority1Client()


@router.get(
    "/document/{bol_number}",
    summary="Download the BOL PDF for a dispatched shipment",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "BOL PDF document"},
        404: {"description": "Document not yet available"},
    },
    dependencies=[Depends(verify_api_key)],
)
async def get_shipment_document(bol_number: str):
    """
    Download the Bill of Lading PDF for a shipment booked through Priority1.

    Call this immediately after a successful `/book` response to retrieve the
    BOL document and attach it to the Odoo order.  Pass the `bol_number`
    returned by `/book`.

    Returns the PDF as `application/pdf` so Odoo can save it directly as a
    binary attachment without any base64 decoding.

    **Header required:** `X-API-Key: <your-key>`
    """
    logger.info(f"Document request for BOL: {bol_number}")
    pdf_bytes = await asyncio.to_thread(_p1.get_shipment_document, bol_number)

    if not pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"BOL document for {bol_number} is not yet available from Priority1. "
                "The document is generated shortly after dispatch — please retry in a few seconds."
            ),
        )

    logger.info(f"Returning BOL PDF for {bol_number} ({len(pdf_bytes)} bytes)")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="BOL_{bol_number}.pdf"'},
    )


@router.get(
    "/invoice",
    response_model=InvoiceResponse,
    summary="Retrieve the freight invoice for a completed shipment",
    dependencies=[Depends(verify_api_key)],
)
async def get_invoice(bol_number: str):
    """
    Retrieve the freight invoice for a shipment from Priority1.

    **Important:** The invoice is only available after the shipment has been
    delivered and Priority1 has processed the freight bill — typically 1–3
    business days after delivery.  Call this endpoint from Odoo once the
    shipment status shows as delivered, or on a scheduled basis, to fetch and
    attach the invoice to the order.

    Pass the `bol_number` returned by `/book`.

    **Header required:** `X-API-Key: <your-key>`
    """
    logger.info(f"Invoice request for BOL: {bol_number}")
    result = await asyncio.to_thread(_p1.get_invoice, bol_number)

    if result.get("status") == "error":
        errors = result.get("errors", [])
        # 404 if Priority1 simply has no invoice yet (not an API error)
        if any("404" in e or "not found" in e.lower() for e in errors):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No invoice found for BOL {bol_number}. It may not be available yet.",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Priority1 invoice error: {'; '.join(errors)}",
        )

    invoices = result.get("invoices", [])
    if not invoices:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No invoice available yet for BOL {bol_number}. "
                "Invoices are generated 1–3 business days after delivery."
            ),
        )

    logger.info(f"Returning {len(invoices)} invoice(s) for BOL {bol_number}")
    return InvoiceResponse(
        status="success",
        bol_number=bol_number,
        invoices=invoices,
    )
