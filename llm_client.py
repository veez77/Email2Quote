import json
import logging
import time
from openai import OpenAI

import config

logger = logging.getLogger(__name__)

FREIGHT_FIELDS_SPEC = """Extract these fields (use null if not mentioned):
- origin_city
- origin_state
- origin_zip
- destination_city
- destination_state
- destination_zip
- cargo_description
- weight (weight of the FIRST line item only, in lbs — do NOT sum multiple rows)
- weight_unit (lbs or kg)
- length (in inches — see dimension rules below)
- width (in inches — see dimension rules below)
- height (in inches — see dimension rules below)
- dimension_unit (inches or cm)
- num_pieces (number of pallets/pieces in the FIRST line item only — do NOT sum multiple rows)
- packaging_type (pallet, crate, box, etc.)
- freight_class
- special_requirements (list of strings: e.g. hazmat, liftgate, temperature_controlled, residential_delivery, inside_delivery, appointment)
- pickup_date (YYYY-MM-DD format if mentioned)
- additional_notes (any other relevant info)

DIMENSION EXTRACTION RULES (critical — read carefully):
- Dimensions on a BOL are always listed as Length x Width x Height (L x W x H) in that order.
- Do NOT swap or mix up dimension values between rows.
- Always use the dimensions of the FIRST line item only.
  Example: row 1 is 48x41x45 and row 2 is 48x42x54 → use row 1: length=48, width=41, height=45
- Never mix values from different rows or axes.

Respond ONLY with valid JSON. No markdown, no explanation, just the JSON object."""

FREIGHT_EXTRACTION_PROMPT = """You are a freight logistics expert. Analyze the following email and extract all freight shipment details into a structured JSON format.

{fields_spec}

Email content:
{email_body}"""

FREIGHT_EXTRACTION_WITH_BOL_PROMPT = """You are a freight logistics expert. Analyze the following email AND the attached Bill of Lading (BOL) document. Combine information from both sources to extract all freight shipment details into a structured JSON format.

The BOL document typically contains: shipper/consignee addresses, cargo description, weight, piece count, freight class, and special handling instructions. Use the BOL as the primary source for shipment details, and supplement with any additional info from the email body.

{fields_spec}

Email content:
{email_body}

--- BOL DOCUMENT CONTENT ---
{bol_content}"""


class LLMClient:
    def __init__(self):
        if not config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not set. Add it to your .env file.")
        self.client = OpenAI(
            api_key=config.GROQ_API_KEY,
            base_url=config.GROQ_BASE_URL,
        )

    def parse_freight_details(self, email_body: str, bol_content: str | None = None) -> dict:
        """Send email body (and optional BOL text) to Groq LLM and extract structured freight details."""
        if bol_content:
            logger.info("Sending email + BOL to Groq for freight detail extraction...")
            prompt = FREIGHT_EXTRACTION_WITH_BOL_PROMPT.format(
                fields_spec=FREIGHT_FIELDS_SPEC,
                email_body=email_body,
                bol_content=bol_content,
            )
        else:
            logger.info("Sending email to Groq for freight detail extraction...")
            prompt = FREIGHT_EXTRACTION_PROMPT.format(
                fields_spec=FREIGHT_FIELDS_SPEC,
                email_body=email_body,
            )

        t0 = time.perf_counter()
        response = self.client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are a freight logistics data extraction assistant. Always respond with valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        llm_elapsed = time.perf_counter() - t0

        raw_response = response.choices[0].message.content.strip()
        logger.debug(f"Raw LLM response: {raw_response}")

        # Strip markdown code fences if present
        if raw_response.startswith("```"):
            lines = raw_response.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_response = "\n".join(lines)

        try:
            parsed = json.loads(raw_response)
            logger.info(f"Successfully parsed freight details from LLM response (LLM took {llm_elapsed:.2f}s).")
            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.error(f"Raw response was: {raw_response}")
            return {"error": "Failed to parse LLM response", "raw_response": raw_response}
