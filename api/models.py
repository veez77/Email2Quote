from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class FreightDetails(BaseModel):
    origin_city: Optional[str] = None
    origin_state: Optional[str] = None
    origin_zip: Optional[str] = None
    destination_city: Optional[str] = None
    destination_state: Optional[str] = None
    destination_zip: Optional[str] = None
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
    special_requirements: list[str] = []
    pickup_date: Optional[str] = None
    additional_notes: Optional[str] = None


class CarrierQuote(BaseModel):
    carrier_name: str
    carrier_code: Optional[str] = None
    service_level: str
    service_level_description: Optional[str] = None
    transit_days: Optional[int] = None
    delivery_date: Optional[str] = None
    total_charge: Optional[float] = None
    currency: str = "USD"
    quote_id: Optional[str] = None
    carrier_quote_number: Optional[str] = None
    valid_until: Optional[str] = None
    charges: list[dict] = []          # breakdown: [{"code": "FSC", "description": "...", "amount": 12.50}]


class QuoteResponse(BaseModel):
    status: str                          # "success" | "placeholder" | "error"
    request_id: str                      # UUID per API call
    extracted_details: FreightDetails
    quotes: list[CarrierQuote] = []
    errors: list[str] = []
    processing_notes: list[str] = []
