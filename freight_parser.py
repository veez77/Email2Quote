import os
import logging
from dataclasses import dataclass, field

import pdfplumber

from gmail_client import GmailClient
from llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class FreightRequest:
    origin_company: str | None = None
    origin_city: str | None = None
    origin_state: str | None = None
    origin_zip: str | None = None
    origin_phone: str | None = None
    destination_company: str | None = None
    destination_city: str | None = None
    destination_state: str | None = None
    destination_zip: str | None = None
    destination_phone: str | None = None
    cargo_description: str | None = None
    weight: float | None = None
    weight_unit: str = "lbs"
    length: float | None = None
    width: float | None = None
    height: float | None = None
    dimension_unit: str = "inches"
    num_pieces: int | None = None
    packaging_type: str | None = None
    freight_class: str | None = None
    special_requirements: list[str] = field(default_factory=list)
    pickup_date: str | None = None
    additional_notes: str | None = None

    # Email metadata
    email_id: str | None = None
    email_subject: str | None = None
    email_sender: str | None = None

    @classmethod
    def from_dict(cls, data: dict, **email_metadata) -> "FreightRequest":
        """Create a FreightRequest from a parsed LLM response dict."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        filtered.update(email_metadata)
        return cls(**filtered)

    def summary(self) -> str:
        """Human-readable summary of the freight request."""
        lines = ["=" * 50, "FREIGHT REQUEST DETAILS", "=" * 50]

        if self.email_sender:
            lines.append(f"  From:        {self.email_sender}")
        if self.email_subject:
            lines.append(f"  Subject:     {self.email_subject}")

        lines.append("")

        origin = ", ".join(filter(None, [self.origin_city, self.origin_state, self.origin_zip]))
        dest = ", ".join(filter(None, [self.destination_city, self.destination_state, self.destination_zip]))
        if self.origin_company:
            lines.append(f"  Shipper:     {self.origin_company}")
        lines.append(f"  Origin:      {origin or 'N/A'}")
        if self.origin_phone:
            lines.append(f"  Shipper Ph:  {self.origin_phone}")
        if self.destination_company:
            lines.append(f"  Consignee:   {self.destination_company}")
        lines.append(f"  Destination: {dest or 'N/A'}")
        if self.destination_phone:
            lines.append(f"  Consignee Ph:{self.destination_phone}")
        lines.append("")

        if self.cargo_description:
            lines.append(f"  Cargo:       {self.cargo_description}")
        if self.weight:
            lines.append(f"  Weight:      {self.weight} {self.weight_unit}")
        if self.length and self.width and self.height:
            lines.append(f"  Dimensions:  {self.length} x {self.width} x {self.height} {self.dimension_unit}")
        if self.num_pieces:
            lines.append(f"  Pieces:      {self.num_pieces} {self.packaging_type or 'unit(s)'}")
        if self.freight_class:
            lines.append(f"  Class:       {self.freight_class}")
        if self.pickup_date:
            lines.append(f"  Pickup Date: {self.pickup_date}")

        if self.special_requirements:
            lines.append(f"  Special:     {', '.join(self.special_requirements)}")
        if self.additional_notes:
            lines.append(f"  Notes:       {self.additional_notes}")

        lines.append("=" * 50)
        return "\n".join(lines)


# NMFC density → freight class lookup table (density in lbs per cubic foot)
_CLASS_TABLE = [
    (50.0,  "50"),
    (35.0,  "55"),
    (30.0,  "60"),
    (22.5,  "65"),
    (15.0,  "70"),
    (13.5,  "77.5"),
    (12.0,  "85"),
    (10.5,  "92.5"),
    (9.0,   "100"),
    (8.0,   "110"),
    (7.0,   "125"),
    (6.0,   "150"),
    (5.0,   "175"),
    (4.0,   "200"),
    (3.0,   "250"),
    (2.0,   "300"),
    (1.0,   "400"),
    (0.0,   "500"),
]


def calculate_freight_class(
    weight_lbs: float,
    length_in: float,
    width_in: float,
    height_in: float,
    num_pieces: int = 1,
) -> tuple[str, float]:
    """Calculate NMFC freight class from density.

    density = total_weight / (num_pieces × volume_per_piece)
    Dimensions are per-piece (per pallet); weight is the total for all pieces.

    Returns (class_string, density_lbs_per_cuft).
    """
    if not all([weight_lbs, length_in, width_in, height_in]):
        return None, None

    pieces = max(num_pieces or 1, 1)
    volume_per_piece_cu_ft = (length_in * width_in * height_in) / 1728.0
    total_volume_cu_ft = pieces * volume_per_piece_cu_ft

    if total_volume_cu_ft <= 0:
        return None, None

    density = weight_lbs / total_volume_cu_ft

    for threshold, cls in _CLASS_TABLE:
        if density >= threshold:
            return cls, round(density, 2)

    return "500", round(density, 2)


def compare_freight_class(fr: "FreightRequest") -> str:
    """Return a one-line class comparison string: BOL stated vs density-calculated."""
    calc_class, density = calculate_freight_class(
        fr.weight, fr.length, fr.width, fr.height, fr.num_pieces
    )
    bol_class = str(fr.freight_class).strip() if fr.freight_class else "N/A"

    if calc_class is None:
        return "  Class Check: insufficient data for density calculation."

    match = "MATCH" if calc_class == bol_class else "MISMATCH"
    return (
        f"  Class Check: BOL={bol_class}  |  Calculated={calc_class} "
        f"(density {density} lbs/cu ft, {fr.weight} lbs / {fr.num_pieces or 1} pcs, "
        f"{fr.length}x{fr.width}x{fr.height} in)  ->  {match}"
    )


BOL_EXTENSIONS = {".pdf", ".PDF"}
BOL_KEYWORDS = {"bol", "bill of lading", "b/l", "shipping"}


def _is_bol_attachment(filename: str) -> bool:
    """Check if an attachment is likely a BOL based on filename."""
    name_lower = filename.lower()
    _, ext = os.path.splitext(name_lower)
    if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return False
    # Accept any PDF/image attachment — BOLs come with various names
    return True


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    text_parts = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {i + 1} ---\n{page_text}")
            logger.info(f"Extracted text from {len(pdf.pages)} PDF page(s).")
    except Exception as e:
        logger.error(f"Failed to extract text from PDF '{file_path}': {e}")
    return "\n\n".join(text_parts)


def _extract_text_from_file(file_path: str) -> str | None:
    """Extract text from an attachment file based on its type."""
    _, ext = os.path.splitext(file_path.lower())
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path)
        if text.strip():
            return text
        logger.warning(f"PDF '{file_path}' has no extractable text (may be a scanned image).")
        return None
    # For image files, we can't extract text without OCR — skip for now
    logger.info(f"Skipping non-PDF attachment: {file_path}")
    return None


def process_email(gmail_client: GmailClient, llm_client: LLMClient, message: dict) -> FreightRequest | None:
    """Process a single email: extract body + BOL attachment, parse with LLM, return structured freight request."""
    message_id = message["id"]
    subject = gmail_client.get_email_subject(message)
    sender = gmail_client.get_email_sender(message)

    logger.info(f"Processing email: '{subject}' from {sender}")

    body = gmail_client.get_email_body(message)

    # Check for BOL attachments first (before checking body — email may have only a BOL)
    bol_content = None
    attachments = gmail_client.get_attachments(message)
    if attachments:
        logger.info(f"Found {len(attachments)} attachment(s): {[a['filename'] for a in attachments]}")
        for att in attachments:
            if _is_bol_attachment(att["filename"]):
                logger.info(f"Downloading BOL attachment: {att['filename']}")
                file_path = gmail_client.download_attachment(
                    att["message_id"], att["attachment_id"], att["filename"]
                )
                extracted = _extract_text_from_file(file_path)
                if extracted:
                    bol_content = extracted
                    logger.info(f"Extracted {len(extracted)} chars from BOL: {att['filename']}")
                    break  # Use the first successfully parsed BOL
    else:
        logger.info("No attachments found in email.")

    if not body.strip() and not bol_content:
        logger.warning(f"Email {message_id} has no text body and no parseable BOL attachment. Skipping.")
        return None

    parsed = llm_client.parse_freight_details(body, bol_content=bol_content)

    if "error" in parsed:
        logger.error(f"LLM parsing failed for email {message_id}: {parsed}")
        return None

    freight_request = FreightRequest.from_dict(
        parsed,
        email_id=message_id,
        email_subject=subject,
        email_sender=sender,
    )

    logger.info(f"Successfully parsed freight request from email {message_id}")
    return freight_request
