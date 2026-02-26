from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import requests

import config
from freight_parser import FreightRequest

if TYPE_CHECKING:
    from api.models import BookingRequest, ContactInfo

logger = logging.getLogger(__name__)

# Maps our special_requirements strings → Priority1 accessorial service codes
ACCESSORIAL_MAP = {
    "liftgate":               "LGPU",   # Liftgate Pickup
    "liftgate_pickup":        "LGPU",
    "liftgate_delivery":      "LGDEL",
    "residential_delivery":   "RES",
    "residential":            "RES",
    "inside_delivery":        "IDEL",
    "inside_pickup":          "IPU",
    "appointment":            "APPT",
    "limited_access":         "LAD",
    "trade_show":             "TSHOW",
    "notify_before_delivery": "NTFY",
}

# Maps our packaging_type strings → Priority1 packaging types
PACKAGING_MAP = {
    "pallet":   "Pallet",
    "pallets":  "Pallet",
    "skid":     "Pallet",
    "crate":    "Crate",
    "box":      "Box",
    "boxes":    "Box",
    "drum":     "Drum",
    "roll":     "Roll",
    "bundle":   "Bundle",
    "bag":      "Bag",
    "piece":    "Piece",
    "pieces":   "Piece",
}


class Priority1Client:
    """Priority1 freight quoting API client.

    Docs: https://dev-api.priority1.com/docs/index.html
    Endpoint: https://dev-api.priority1.com
    """

    # Class-level cache: quote_id (str) → line item dict that was sent to Priority1.
    # Shared across all instances so /quote and /book routes (separate instances) see the same data.
    _quote_item_cache: dict[str, dict] = {}

    def __init__(self):
        if not config.PRIORITY1_API_KEY:
            raise ValueError("PRIORITY1_API_KEY not set. Add it to your .env file.")
        self.base_url = config.PRIORITY1_API_URL.rstrip("/")
        self.headers = {
            "X-Api-Key": config.PRIORITY1_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_quote(self, freight_request: FreightRequest) -> dict:
        """Submit a freight request to Priority1 and return carrier quotes."""
        if not freight_request.origin_zip or not freight_request.destination_zip:
            logger.warning("Missing origin or destination zip — cannot get Priority1 quote.")
            return {
                "status": "error",
                "quotes": [],
                "errors": ["Missing origin or destination zip code."],
                "processing_notes": [],
            }

        payload = self._build_payload(freight_request)
        quote_item = payload["items"][0]  # exact item sent — cache for dispatch replay
        logger.info(
            f"Requesting Priority1 rates: {freight_request.origin_zip} → "
            f"{freight_request.destination_zip}, "
            f"{freight_request.weight} {freight_request.weight_unit}"
        )

        try:
            response = requests.post(
                f"{self.base_url}/v2/ltl/quotes/rates",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            result = self._parse_response(data)
            # Cache the item for every returned quote_id so dispatch can replay it exactly
            for q in result.get("quotes", []):
                if q.get("quote_id"):
                    Priority1Client._quote_item_cache[q["quote_id"]] = quote_item
            return result

        except requests.HTTPError as e:
            # NOTE: e.response is falsy for 4xx/5xx — use `is not None` not bool check
            body = e.response.text if e.response is not None else "no body"
            status_code = e.response.status_code if e.response is not None else "?"
            logger.error(f"Priority1 API HTTP error {status_code}: {body}")
            return {
                "status": "error",
                "quotes": [],
                "errors": [f"Priority1 API error {status_code}: {body}"],
                "processing_notes": [],
            }
        except requests.RequestException as e:
            logger.error(f"Priority1 API request failed: {e}")
            return {
                "status": "error",
                "quotes": [],
                "errors": [str(e)],
                "processing_notes": [],
            }

    def _build_payload(self, fr: FreightRequest) -> dict:
        """Build the Priority1 LTL rate quote request payload."""
        # Pickup date: use parsed date or default to tomorrow
        if fr.pickup_date:
            try:
                pickup_dt = datetime.strptime(fr.pickup_date, "%Y-%m-%d")
            except ValueError:
                pickup_dt = datetime.now() + timedelta(days=1)
        else:
            pickup_dt = datetime.now() + timedelta(days=1)

        # Build items array
        item = {
            "freightClass": str(fr.freight_class) if fr.freight_class else "70",
            "packagingType": self._map_packaging(fr.packaging_type),
            "units": fr.num_pieces or 1,
            "pieces": fr.num_pieces or 1,
            "totalWeight": float(fr.weight) if fr.weight else 100.0,
            "isStackable": False,
            "isHazardous": "hazmat" in (fr.special_requirements or []),
            "isUsed": False,
            "isMachinery": False,
        }
        if fr.cargo_description:
            item["description"] = fr.cargo_description
        if fr.length:
            item["length"] = float(fr.length)
        if fr.width:
            item["width"] = float(fr.width)
        if fr.height:
            item["height"] = float(fr.height)
        # Dimensions are cached with the item and replayed exactly at dispatch time,
        # so Priority1's "items must match quote" check will pass.

        # Build accessorial services list
        accessorials = []
        for req in (fr.special_requirements or []):
            code = ACCESSORIAL_MAP.get(req.lower())
            if code:
                accessorials.append({"code": code})

        payload = {
            "originZipCode": fr.origin_zip,
            "destinationZipCode": fr.destination_zip,
            "pickupDate": pickup_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "items": [item],
        }
        if fr.origin_city:
            payload["originCity"] = fr.origin_city
        if fr.origin_state:
            payload["originStateAbbreviation"] = fr.origin_state
        if fr.destination_city:
            payload["destinationCity"] = fr.destination_city
        if fr.destination_state:
            payload["destinationStateAbbreviation"] = fr.destination_state
        if accessorials:
            payload["accessorialServices"] = accessorials

        return payload

    def _map_packaging(self, packaging_type: str | None) -> str:
        if not packaging_type:
            return "Pallet"
        return PACKAGING_MAP.get(packaging_type.lower(), "Pallet")

    def _parse_response(self, data: dict) -> dict:
        """Parse Priority1 rate quote response into our standard format."""
        rate_quotes = data.get("rateQuotes", [])
        invalid_quotes = data.get("invalidRateQuotes", [])
        processing_notes = []

        # Log any carriers that failed to return rates
        for inv in invalid_quotes:
            carrier = inv.get("carrierName", inv.get("carrierCode", "Unknown"))
            errors = "; ".join(
                m.get("text", "") for m in inv.get("errorMessages", [])
            )
            msg = f"{carrier}: {errors}" if errors else f"{carrier}: no rate available"
            logger.warning(f"Carrier skipped — {msg}")
            processing_notes.append(f"Carrier skipped — {msg}")

        if not rate_quotes:
            logger.warning("Priority1 returned no successful rate quotes.")
            return {
                "status": "success",
                "quotes": [],
                "errors": [],
                "processing_notes": processing_notes or ["Priority1 returned no available rates for this lane."],
            }

        now = datetime.utcnow()
        quotes = []
        for rq in rate_quotes:
            # Skip quotes that have already expired — they cannot be booked
            expiration_raw = rq.get("expirationDate")
            if expiration_raw:
                try:
                    exp_dt = datetime.fromisoformat(expiration_raw.rstrip("Z").split(".")[0])
                    if exp_dt < now:
                        carrier = rq.get("carrierName", rq.get("carrierCode", "Unknown"))
                        logger.warning(
                            f"Skipping expired quote from {carrier} "
                            f"(id={rq.get('id')}, expired {expiration_raw})"
                        )
                        processing_notes.append(
                            f"Carrier skipped — {carrier}: quote expired ({expiration_raw[:10]})"
                        )
                        continue
                except (ValueError, TypeError):
                    pass  # unparseable expiry — include the quote and let Priority1 reject if needed

            detail = rq.get("rateQuoteDetail", {})
            quotes.append({
                "carrier_name": rq.get("carrierName", ""),
                "carrier_code": rq.get("carrierCode", ""),
                "service_level": rq.get("serviceLevel", ""),
                "service_level_description": rq.get("serviceLevelDescription", ""),
                "transit_days": rq.get("transitDays"),
                "delivery_date": rq.get("deliveryDate"),
                "total_charge": detail.get("total"),
                "currency": "USD",
                "quote_id": str(rq.get("id", "")),
                "valid_until": expiration_raw,
                "carrier_quote_number": rq.get("carrierQuoteNumber"),
                "charges": detail.get("charges", []),
            })

        logger.info(f"Priority1 returned {len(quotes)} valid (non-expired) rate quote(s).")
        processing_notes.insert(0, f"Received {len(quotes)} rate(s) from Priority1.")
        return {
            "status": "success",
            "quotes": quotes,
            "errors": [],
            "processing_notes": processing_notes,
        }

    # ------------------------------------------------------------------
    # Dispatch (booking)
    # ------------------------------------------------------------------

    def dispatch_shipment(self, req: "BookingRequest") -> dict:
        """Book a shipment against an existing rate quote on Priority1.

        Returns a dict with keys: status, shipment_id, bol_number,
        pickup_number, bol_url, pallet_label_url, estimated_delivery, errors.
        """
        # Look up the exact line item that was sent when this quote was obtained.
        # This guarantees Priority1's "items must match quote" validation passes.
        cached_item = Priority1Client._quote_item_cache.get(str(req.quote_id))
        if not cached_item:
            logger.error(
                f"No cached item for quote_id {req.quote_id}. "
                "Server may have restarted — please re-request a quote."
            )
            return {
                "status": "error",
                "errors": [
                    f"Quote {req.quote_id} not found in server cache. "
                    "Please re-request a quote and try again."
                ],
            }

        payload = self._build_dispatch_payload(req, cached_item)
        logger.info(
            f"Dispatching shipment: quoteId={req.quote_id}  "
            f"{req.shipper.zip} → {req.consignee.zip}  "
            f"pickup={payload.get('pickupWindow', {}).get('date', 'N/A')}"
        )
        import json as _json
        logger.info(f"Dispatch payload:\n{_json.dumps(payload, indent=2)}")
        try:
            response = requests.post(
                f"{self.base_url}/v2/ltl/shipments/dispatch",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            # Use response.text directly (not bool check — 4xx responses are falsy)
            if not response.ok:
                body = response.text or "(empty body)"
                logger.error(
                    f"Priority1 dispatch HTTP error {response.status_code}: {body}"
                )
                return {
                    "status": "error",
                    "errors": [f"Priority1 dispatch error {response.status_code}: {body}"],
                }
            return self._parse_dispatch_response(response.json())

        except requests.RequestException as e:
            logger.error(f"Priority1 dispatch request failed: {e}")
            return {"status": "error", "errors": [str(e)]}

    def _build_dispatch_payload(self, req: "BookingRequest", cached_item: dict) -> dict:
        """Build the Priority1 DispatchShipmentRequest payload."""
        # Pickup date: convert YYYY-MM-DD → MM/DD/YYYY; auto-advance if in the past
        try:
            pickup_dt = datetime.strptime(req.pickup_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            pickup_dt = datetime.now() + timedelta(days=1)
        if pickup_dt.date() < datetime.now().date():
            logger.warning(
                f"Pickup date {req.pickup_date} is in the past — advancing to tomorrow."
            )
            pickup_dt = datetime.now() + timedelta(days=1)
        pickup_date_str = pickup_dt.strftime("%m/%d/%Y")

        # Delivery date: default to pickup + 7 days (Priority1 needs a window)
        delivery_dt = pickup_dt + timedelta(days=7)
        delivery_date_str = delivery_dt.strftime("%m/%d/%Y")

        # Replay the exact item that was sent at quote time (Priority1 validates they match).
        # Add description — required by dispatch but optional at quote time.
        item = dict(cached_item)
        item.pop("isMachinery", None)  # dispatch endpoint doesn't accept this field
        if not item.get("description"):
            item["description"] = "General Freight"

        accessorials = item.pop("accessorialServices", [])  # pull out if accidentally stored

        payload: dict = {
            "quoteId": int(req.quote_id),
            "originLocation": self._build_location(req.shipper),
            "destinationLocation": self._build_location(req.consignee),
            "lineItems": [item],
            "pickupWindow": {
                "date": pickup_date_str,
                "startTime": req.pickup_start_time,
                "endTime": req.pickup_end_time,
            },
            "deliveryWindow": {
                "date": delivery_date_str,
                "startTime": req.delivery_start_time,
                "endTime": req.delivery_end_time,
            },
        }
        if req.pickup_note:
            payload["pickupNote"] = req.pickup_note
        if req.delivery_note:
            payload["deliveryNote"] = req.delivery_note
        if accessorials:
            payload["accessorialServices"] = accessorials

        # Add reference number as a shipment identifier
        identifiers = []
        if req.reference_number:
            identifiers.append({
                "type": "CUSTOMER_REFERENCE",
                "value": req.reference_number,
                "primaryForType": True,
            })
        if identifiers:
            payload["shipmentIdentifiers"] = identifiers

        return payload

    @staticmethod
    def _build_location(contact: "ContactInfo") -> dict:
        # Priority1 expects exactly 10-digit US NANP phone numbers.
        # NANP area code rules: first digit 2–9, second digit 0–8.
        import re as _re
        phone_digits = _re.sub(r"\D", "", contact.phone or "")
        if len(phone_digits) == 11 and phone_digits.startswith("1"):
            phone_digits = phone_digits[1:]  # strip leading country code
        # Validate: 10 digits AND NANP area code (NXX: N=2-9, X=0-8)
        nanp_ok = (
            len(phone_digits) == 10
            and phone_digits[0] in "23456789"
            and phone_digits[1] in "012345678"
        )
        if not nanp_ok:
            logger.warning(
                f"Phone '{contact.phone}' for '{contact.company_name}' is not a valid "
                f"NANP US number — using placeholder. "
                f"(Odoo should pass extracted_details.origin_phone / destination_phone from the quote response.)"
            )
            phone_digits = "8005551234"  # recognisable placeholder
        loc: dict = {
            "address": {
                "addressLine1": contact.address_line1,
                "city": contact.city,
                "state": contact.state,
                "postalCode": contact.zip,
                "country": contact.country,
            },
            "contact": {
                "companyName": contact.company_name,
                "phoneNumber": phone_digits,
            },
        }
        if contact.address_line2:
            loc["address"]["addressLine2"] = contact.address_line2
        if contact.contact_name:
            loc["contact"]["contactName"] = contact.contact_name
        if contact.email:
            loc["contact"]["email"] = contact.email
        return loc

    # ------------------------------------------------------------------
    # Documents & Invoices
    # ------------------------------------------------------------------

    def get_shipment_document(self, bol_number: str) -> bytes | None:
        """Fetch the BOL PDF for a dispatched shipment from Priority1.

        Calls POST /v2/ltl/shipments/images to get the document URL, then
        downloads the PDF bytes.  Returns None if unavailable.
        """
        try:
            resp = requests.post(
                f"{self.base_url}/v2/ltl/shipments/images",
                headers=self.headers,
                json={"bolNumber": bol_number, "imageFormatTypeId": "PDF"},
                timeout=30,
            )
            if not resp.ok:
                logger.warning(
                    f"Priority1 images API returned {resp.status_code} for BOL {bol_number}: {resp.text[:200]}"
                )
                return None
            image_url = resp.json().get("imageUrl")
            if not image_url:
                logger.warning(f"No imageUrl in Priority1 images response for BOL {bol_number}")
                return None
            # Download the actual PDF bytes (Priority1 pre-signed URL — no auth header needed)
            pdf_resp = requests.get(image_url, timeout=30)
            if not pdf_resp.ok:
                logger.warning(f"Failed to download BOL PDF from {image_url}: {pdf_resp.status_code}")
                return None
            logger.info(f"Downloaded BOL PDF for {bol_number}: {len(pdf_resp.content)} bytes")
            return pdf_resp.content
        except requests.RequestException as e:
            logger.error(f"Error fetching shipment document for BOL {bol_number}: {e}")
            return None

    def get_invoice(self, bol_number: str) -> dict:
        """Fetch the freight invoice for a completed shipment from Priority1.

        Calls GET /v2/admin/customerinvoices?bolNumber={bol_number}.
        The invoice is only available after the shipment has been delivered
        and Priority1 has processed the freight bill.

        Returns a dict with keys: status, invoices (list), errors.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/v2/admin/customerinvoices",
                headers=self.headers,
                params={"bolNumber": bol_number},
                timeout=30,
            )
            if not resp.ok:
                body = resp.text or "(empty)"
                logger.error(f"Priority1 invoice API {resp.status_code} for BOL {bol_number}: {body[:300]}")
                return {
                    "status": "error",
                    "invoices": [],
                    "errors": [f"Priority1 invoice API error {resp.status_code}: {body}"],
                }
            data = resp.json()
            invoices = data.get("customerInvoices", [])
            logger.info(f"Priority1 returned {len(invoices)} invoice(s) for BOL {bol_number}")
            return {"status": "success", "invoices": invoices, "errors": []}
        except requests.RequestException as e:
            logger.error(f"Error fetching invoice for BOL {bol_number}: {e}")
            return {"status": "error", "invoices": [], "errors": [str(e)]}

    @staticmethod
    def _parse_dispatch_response(data: dict) -> dict:
        """Parse Priority1 DispatchShipmentResponse into our standard format."""
        shipment_id = str(data.get("id", "")) if data.get("id") else None

        bol_number = None
        pickup_number = None
        for ident in data.get("shipmentIdentifiers", []):
            id_type = ident.get("type", "")
            if id_type == "BILL_OF_LADING" and ident.get("primaryForType"):
                bol_number = ident.get("value")
            elif id_type == "PICKUP" and ident.get("primaryForType"):
                pickup_number = ident.get("value")

        messages = data.get("infoMessages", [])
        errors = [m["text"] for m in messages if m.get("severity") == "Error"]
        notes  = [m["text"] for m in messages if m.get("severity") != "Error"]

        logger.info(
            f"Shipment dispatched — ID: {shipment_id}  BOL: {bol_number}  Pickup: {pickup_number}"
        )
        return {
            "status": "booked",
            "shipment_id": shipment_id,
            "bol_number": bol_number,
            "pickup_number": pickup_number,
            "bol_url": data.get("capacityProviderBolUrl"),
            "pallet_label_url": data.get("capacityProviderPalletLabelUrl"),
            "estimated_delivery": data.get("estimatedDeliveryDate"),
            "errors": errors,
            "processing_notes": notes,
        }
