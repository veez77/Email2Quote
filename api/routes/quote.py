import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, status

import config
from api.dependencies import verify_api_key
from api.models import CarrierQuote, FreightDetails, QuoteResponse
from freight_parser import FreightRequest, extract_text_from_pdf
from llm_client import LLMClient
from priority1_client import Priority1Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quote", tags=["Quote"])

# Shared instances (stateless — safe to share across requests)
_llm = LLMClient()
_p1 = Priority1Client()


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
    logger.info(f"[{request_id}] Received BOL upload: {file.filename} ({len(contents)} bytes)")

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
        p1_result = await asyncio.to_thread(_p1.get_quote, freight)
        logger.info(f"[{request_id}] Quote complete. Status: {p1_result.get('status')}")
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
    logger.info(f"[{request_id}] Received text quote request ({len(body)} chars)")

    parsed = await asyncio.to_thread(_llm.parse_freight_details, body)
    if "error" in parsed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM extraction failed: {parsed.get('error')}",
        )

    freight = FreightRequest.from_dict(parsed)
    p1_result = await asyncio.to_thread(_p1.get_quote, freight)
    logger.info(f"[{request_id}] Quote complete. Status: {p1_result.get('status')}")
    return _build_response(request_id, freight, p1_result)
