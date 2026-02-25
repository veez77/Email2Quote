import logging
from datetime import datetime, timedelta

import requests

import config
from freight_parser import FreightRequest

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

    # Dev-mode normalization: these values are known to return quotes from the
    # Priority1 dev environment for most lanes.  They replace the actual shipment
    # parameters only when hitting the dev endpoint so the rest of the pipeline
    # (email, LLM, FreightRequest) is completely unaffected.
    _DEV_WEIGHT_LBS = 500.0
    _DEV_PIECES     = 1
    _DEV_LENGTH     = 48.0
    _DEV_WIDTH      = 40.0
    _DEV_HEIGHT     = 48.0

    def __init__(self):
        if not config.PRIORITY1_API_KEY:
            raise ValueError("PRIORITY1_API_KEY not set. Add it to your .env file.")
        self.base_url = config.PRIORITY1_API_URL.rstrip("/")
        self.is_dev = "dev-api" in self.base_url
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
            return self._parse_response(data)

        except requests.HTTPError as e:
            body = e.response.text if e.response else "no body"
            logger.error(f"Priority1 API HTTP error {e.response.status_code}: {body}")
            return {
                "status": "error",
                "quotes": [],
                "errors": [f"Priority1 API error {e.response.status_code}: {body}"],
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

        # Dev environment: normalize to parameters that have carrier coverage.
        # Priority1 dev only has rate tables for small-to-medium LTL shipments.
        if self.is_dev:
            logger.warning(
                "DEV MODE: Overriding shipment parameters for Priority1 dev environment "
                f"(actual: {item['totalWeight']} lbs / {item['units']} pcs  ->  "
                f"dev: {self._DEV_WEIGHT_LBS} lbs / {self._DEV_PIECES} pcs)"
            )
            item["totalWeight"] = self._DEV_WEIGHT_LBS
            item["units"]       = self._DEV_PIECES
            item["pieces"]      = self._DEV_PIECES
            item["length"]      = self._DEV_LENGTH
            item["width"]       = self._DEV_WIDTH
            item["height"]      = self._DEV_HEIGHT

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

        quotes = []
        for rq in rate_quotes:
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
                "valid_until": rq.get("expirationDate"),
                "carrier_quote_number": rq.get("carrierQuoteNumber"),
                "charges": detail.get("charges", []),
            })

        logger.info(f"Priority1 returned {len(quotes)} rate quote(s).")
        processing_notes.insert(0, f"Received {len(quotes)} rate(s) from Priority1.")
        return {
            "status": "success",
            "quotes": quotes,
            "errors": [],
            "processing_notes": processing_notes,
        }
