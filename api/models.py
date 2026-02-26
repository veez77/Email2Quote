from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# STEP 1: Quote models
#
# Used by POST /quote/bol and POST /quote/text.
# Email2Quote extracts freight details from the BOL/email (via LLM), fetches
# carrier rates from Priority1, and returns a QuoteResponse.
# The caller (Odoo) displays quotes to the user and stores the selected quote_id.
# ---------------------------------------------------------------------------

class FreightDetails(BaseModel):
    """Freight shipment details extracted from a BOL or email by the LLM.

    Returned inside QuoteResponse.extracted_details so the caller can
    display or store the parsed values (e.g. to pre-fill a shipment form).
    These are also the values that were submitted to Priority1 to obtain rates.
    """
    origin_company: Optional[str] = None
    origin_city: Optional[str] = None
    origin_state: Optional[str] = None
    origin_zip: Optional[str] = None
    origin_phone: Optional[str] = None      # digits only, from BOL Shipper section
    destination_company: Optional[str] = None
    destination_city: Optional[str] = None
    destination_state: Optional[str] = None
    destination_zip: Optional[str] = None
    destination_phone: Optional[str] = None  # digits only, from BOL Consignee section
    cargo_description: Optional[str] = None
    weight: Optional[float] = None
    weight_unit: str = "lbs"
    length: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    dimension_unit: str = "inches"
    num_pieces: Optional[int] = None
    packaging_type: Optional[str] = None
    freight_class: Optional[str] = None

    @field_validator("freight_class", mode="before")
    @classmethod
    def coerce_freight_class(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)

    special_requirements: list[str] = []
    pickup_date: Optional[str] = None
    additional_notes: Optional[str] = None


class CarrierQuote(BaseModel):
    """A single carrier rate returned by Priority1."""
    carrier_name: str
    carrier_code: Optional[str] = None
    service_level: str
    service_level_description: Optional[str] = None
    transit_days: Optional[int] = None
    delivery_date: Optional[str] = None
    total_charge: Optional[float] = None
    currency: str = "USD"
    quote_id: Optional[str] = None          # Priority1 quote ID — pass to /book to confirm
    carrier_quote_number: Optional[str] = None
    valid_until: Optional[str] = None
    charges: list[dict] = []                # breakdown: [{"code": "FSC", "description": "...", "amount": 12.50}]


class QuoteResponse(BaseModel):
    """Response from POST /quote/bol or POST /quote/text."""
    status: str                             # "success" | "error"
    request_id: str                         # UUID per API call
    extracted_details: FreightDetails       # what the LLM parsed from the BOL/email
    quotes: list[CarrierQuote] = []         # carrier rates from Priority1
    errors: list[str] = []
    processing_notes: list[str] = []


# ---------------------------------------------------------------------------
# STEP 2: Booking models
#
# Used by POST /book.
# The caller provides the chosen quote_id plus shipper/consignee addresses and
# a pickup date.  Email2Quote retrieves the freight line-item details from its
# server-side cache (keyed by quote_id) and submits a dispatch request to
# Priority1.  No freight details need to be re-sent by the caller.
# ---------------------------------------------------------------------------

class ContactInfo(BaseModel):
    """Shipper or consignee contact + address.

    Provide the actual warehouse/facility address and contact details.
    Use extracted_details.origin_phone / destination_phone from the QuoteResponse
    for the shipper and consignee phone numbers (parsed from the BOL by the LLM).
    """
    company_name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    zip: str
    country: str = "US"
    contact_name: Optional[str] = None
    phone: Optional[str] = None             # 10-digit US number; falls back to placeholder if invalid/missing
    email: Optional[str] = None


class BookingRequest(BaseModel):
    """Request body for POST /book (Step 2 — dispatch confirmation).

    Only the selected quote_id, shipper/consignee addresses, and pickup window
    are required.  The freight line-item details (weight, class, dimensions, etc.)
    are looked up from the server-side quote cache — do NOT re-send them.
    """
    quote_id: str                           # CarrierQuote.quote_id returned by /quote/bol or /quote/text

    shipper: ContactInfo                    # origin warehouse address + contact
    consignee: ContactInfo                  # destination address + contact

    pickup_date: str                        # YYYY-MM-DD
    pickup_start_time: str = "08:00"        # HH:MM (24-hour)
    pickup_end_time: str = "16:00"
    delivery_start_time: str = "08:00"
    delivery_end_time: str = "17:00"

    reference_number: Optional[str] = None  # Odoo PO/SO reference
    pickup_note: Optional[str] = None
    delivery_note: Optional[str] = None


class BookingResponse(BaseModel):
    """Response from POST /book."""
    status: str                             # "booked" | "error"
    shipment_id: Optional[str] = None       # Priority1 internal shipment ID
    bol_number: Optional[str] = None        # Bill of Lading number
    pickup_number: Optional[str] = None     # Pickup confirmation number
    bol_url: Optional[str] = None           # URL to download BOL PDF
    pallet_label_url: Optional[str] = None
    carrier_name: Optional[str] = None
    estimated_delivery: Optional[str] = None
    errors: list[str] = []
    processing_notes: list[str] = []
