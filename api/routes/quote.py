import asyncio
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, status

import config
from api.dependencies import verify_api_key
from api.models import CarrierQuote, FreightDetails, QuoteResponse
from freight_parser import FreightRequest, extract_text_from_pdf, compare_freight_class
from llm_client import LLMClient
from priority1_client import Priority1Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quote", tags=["Quote"])

# Shared instances (stateless — safe to share across requests)
_llm = LLMClient()
_p1 = Priority1Client()


def _log_parsed_freight(request_id: str, freight: FreightRequest) -> None:
    """Log extracted freight details to the server console."""
    logger.info(freight.summary())
    logger.info(compare_freight_class(freight))


def _log_quotes(request_id: str, p1_result: dict) -> None:
    """Log Priority1 carrier quotes to the server console (mirrors main._print_quotes)."""
    quotes = p1_result.get("quotes", [])
    errors = p1_result.get("errors", [])
    notes = p1_result.get("processing_notes", [])

    lines = ["=" * 50, "PRIORITY1 CARRIER QUOTES", "=" * 50]
    for err in errors:
        lines.append(f"  ERROR: {err}")
    for note in notes:
        lines.append(f"  Note: {note.split(chr(10))[0][:120]}")
    if not quotes:
        lines.append(f"  Status: {p1_result.get('status', 'unknown')} — no quotes returned.")
    else:
        for i, q in enumerate(quotes, 1):
            lines.append(f"\n  Quote {i}: {q.get('carrier_name', 'Unknown Carrier')}")
            lines.append(f"    Service:      {q.get('service_level', '')} {q.get('service_level_description', '')}")
            lines.append(f"    Transit:      {q.get('transit_days', 'N/A')} day(s)  |  Delivery: {q.get('delivery_date', 'N/A')}")
            lines.append(f"    Total Charge: ${q.get('total_charge') or 0:,.2f}")
            for charge in q.get("charges", []):
                lines.append(
                    f"      {charge.get('code') or '':<8} {charge.get('description') or '':<35} ${charge.get('amount') or 0:>8,.2f}"
                )
            lines.append(f"    Quote ID:     {q.get('quote_id', '')}  |  Valid until: {q.get('valid_until', 'N/A')}")
    lines.append("=" * 50)
    logger.info("\n".join(lines))


def _build_response(request_id: str, freight: FreightRequest, p1_result: dict) -> QuoteResponse:
    """Assemble a QuoteResponse from parsed freight data and Priority1 result."""
    freight_dict = {
        k: v for k, v in asdict(freight).items()
        if k not in {"email_id", "email_subject", "email_sender"}
    }
    extracted = FreightDetails(**freight_dict)

    quotes = [CarrierQuote(**q) for q in p1_result.get("quotes", [])]

    return QuoteResponse(
        status=p1_result.get("status", "error"),
        request_id=request_id,
        extracted_details=extracted,
        quotes=quotes,
        errors=p1_result.get("errors", []),
        processing_notes=p1_result.get("processing_notes", []),
    )


@router.post(
    "/details",
    response_model=QuoteResponse,
    summary="Get freight quote from structured JSON freight details",
    dependencies=[Depends(verify_api_key)],
)
async def quote_from_details(details: FreightDetails):
    """
    Submit structured freight details directly as JSON — no BOL PDF upload, no LLM extraction.

    Use this when freight data is already available in your system (e.g., from an Odoo sale order).
    The JSON body maps directly to the `FreightDetails` schema — all fields are optional except
    `origin_zip` and `destination_zip` which are required by Priority1.

    Returns the same `QuoteResponse` as the other quote endpoints, including `extracted_details`
    (echoing back the submitted values) and `quotes` (carrier rates from Priority1).

    **Header required:** `X-API-Key: <your-key>`\n
    **Content-Type:** `application/json`
    """
    request_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info(f"[{request_id}] NEW REQUEST FROM ODOO TMS — structured JSON details")
    logger.info(
        f"[{request_id}] {details.origin_zip} → {details.destination_zip}  |  "
        f"{details.weight} {details.weight_unit}  |  class {details.freight_class}"
    )
    logger.info("=" * 60)

    t0 = time.perf_counter()

    freight = FreightRequest.from_dict(details.model_dump())
    _log_parsed_freight(request_id, freight)

    p1_result = await asyncio.to_thread(_p1.get_quote, freight)
    _log_quotes(request_id, p1_result)
    elapsed = time.perf_counter() - t0
    logger.info(f"[{request_id}] Details request complete — {len(p1_result.get('quotes', []))} quote(s) in {elapsed:.2f}s")
    return _build_response(request_id, freight, p1_result)


@router.post(
    "/bol",
    response_model=QuoteResponse,
    summary="Get freight quote from a BOL PDF",
    dependencies=[Depends(verify_api_key)],
)
async def quote_from_bol(file: UploadFile = File(..., description="BOL document as PDF")):
    """
    Upload a Bill of Lading (BOL) PDF. The agent extracts the text,
    sends it to the LLM for freight detail parsing, and returns quotes.

    **Header required:** `X-API-Key: <your-key>`
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are accepted.")

    contents = await file.read()
    if len(contents) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {config.MAX_UPLOAD_MB} MB.",
        )

    request_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info(f"[{request_id}] NEW REQUEST FROM ODOO TMS — BOL PDF upload")
    logger.info(f"[{request_id}] File: {file.filename}  |  Size: {len(contents):,} bytes")
    logger.info("=" * 60)

    t0 = time.perf_counter()

    # Write to a named temp file — pdfplumber requires a file path
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        bol_text = await asyncio.to_thread(extract_text_from_pdf, tmp_path)
        if not bol_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="PDF contains no extractable text. It may be a scanned image (OCR not yet supported).",
            )

        parsed = await asyncio.to_thread(_llm.parse_freight_details, "", bol_content=bol_text)
        if "error" in parsed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"LLM extraction failed: {parsed.get('error')}",
            )

        freight = FreightRequest.from_dict(parsed)
        _log_parsed_freight(request_id, freight)

        p1_result = await asyncio.to_thread(_p1.get_quote, freight)
        _log_quotes(request_id, p1_result)
        elapsed = time.perf_counter() - t0
        logger.info(f"[{request_id}] BOL request complete — {len(p1_result.get('quotes', []))} quote(s) in {elapsed:.2f}s")
        return _build_response(request_id, freight, p1_result)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post(
    "/text",
    response_model=QuoteResponse,
    summary="Get freight quote from plain text description",
    dependencies=[Depends(verify_api_key)],
)
async def quote_from_text(body: str = Body(..., media_type="text/plain")):
    """
    Send a plain text freight description (from an email body, form field, etc.).
    The LLM extracts the shipment details and returns quotes.

    **Header required:** `X-API-Key: <your-key>`
    **Content-Type:** `text/plain`
    """
    if not body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body is empty.")

    request_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info(f"[{request_id}] NEW REQUEST FROM ODOO TMS — plain text")
    logger.info(f"[{request_id}] Body ({len(body)} chars): {body[:200]}{'...' if len(body) > 200 else ''}")
    logger.info("=" * 60)

    t0 = time.perf_counter()

    parsed = await asyncio.to_thread(_llm.parse_freight_details, body)
    if "error" in parsed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM extraction failed: {parsed.get('error')}",
        )

    freight = FreightRequest.from_dict(parsed)
    _log_parsed_freight(request_id, freight)

    p1_result = await asyncio.to_thread(_p1.get_quote, freight)
    _log_quotes(request_id, p1_result)
    elapsed = time.perf_counter() - t0
    logger.info(f"[{request_id}] Text request complete — {len(p1_result.get('quotes', []))} quote(s) in {elapsed:.2f}s")
    return _build_response(request_id, freight, p1_result)
