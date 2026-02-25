import logging
import sys
import time

import schedule

import config
from gmail_client import GmailClient
from llm_client import LLMClient
from freight_parser import process_email, compare_freight_class
from priority1_client import Priority1Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _print_quotes(quote_result: dict):
    """Print Priority1 carrier quotes to the console in a readable format."""
    status = quote_result.get("status", "unknown")
    quotes = quote_result.get("quotes", [])
    errors = quote_result.get("errors", [])
    notes = quote_result.get("processing_notes", [])

    print("=" * 50)
    print("PRIORITY1 CARRIER QUOTES")
    print("=" * 50)

    if errors:
        for err in errors:
            print(f"  ERROR: {err}")

    if notes:
        for note in notes:
            # Truncate long notes (e.g. HTML error bodies from failed carriers)
            short = note.split("\n")[0][:120]
            print(f"  Note: {short}")

    if not quotes:
        print(f"  Status: {status} — no quotes returned.")
    else:
        for i, q in enumerate(quotes, 1):
            print(f"\n  Quote {i}: {q.get('carrier_name', 'Unknown Carrier')}")
            print(f"    Service:      {q.get('service_level', '')} {q.get('service_level_description', '')}")
            print(f"    Transit:      {q.get('transit_days', 'N/A')} day(s)  |  Delivery: {q.get('delivery_date', 'N/A')}")
            print(f"    Total Charge: ${q.get('total_charge') or 0:,.2f}")
            charges = q.get("charges", [])
            for charge in charges:
                print(f"      {charge.get('code') or '':<8} {charge.get('description') or '':<35} ${charge.get('amount') or 0:>8,.2f}")
            print(f"    Quote ID:     {q.get('quote_id', '')}  |  Valid until: {q.get('valid_until', 'N/A')}")

    print("=" * 50)


def check_inbox(gmail: GmailClient, llm: LLMClient, priority1: Priority1Client):
    """Check inbox for matching emails and process them."""
    logger.info(f"Checking inbox for '{config.EMAIL_SUBJECT_FILTER}' emails...")

    try:
        messages = gmail.get_unread_emails(config.EMAIL_SUBJECT_FILTER)
    except Exception as e:
        logger.error(f"Failed to fetch emails: {e}")
        return

    for message in messages:
        message_id = message["id"]
        try:
            t0 = time.perf_counter()
            freight_request = process_email(gmail, llm, message)

            if freight_request:
                print(freight_request.summary())
                print(compare_freight_class(freight_request))
                elapsed = time.perf_counter() - t0
                logger.info(f"BOL parsed and printed in {elapsed:.2f}s (LLM + extraction pipeline).")

                quote_result = priority1.get_quote(freight_request)
                _print_quotes(quote_result)

        except Exception as e:
            logger.error(f"Error processing email {message_id}: {e}", exc_info=True)
        finally:
            # Always mark as read — even if parsing or printing failed — to avoid reprocessing
            gmail.mark_as_read(message_id)


def main():
    logger.info("=" * 60)
    logger.info("Email2Quote Agent Starting")
    logger.info(f"  Subject filter: '{config.EMAIL_SUBJECT_FILTER}'")
    logger.info(f"  Poll interval:  {config.POLL_INTERVAL_MINUTES} minutes")
    logger.info(f"  LLM model:      {config.GROQ_MODEL}")
    logger.info("=" * 60)

    gmail = GmailClient()
    llm = LLMClient()
    priority1 = Priority1Client()

    # Run once immediately on startup
    check_inbox(gmail, llm, priority1)
    logger.info(f"Next check in {config.POLL_INTERVAL_MINUTES} minute(s). Running continuously — press Ctrl+C to stop.")

    # Schedule periodic checks
    schedule.every(config.POLL_INTERVAL_MINUTES).minutes.do(
        check_inbox, gmail, llm, priority1
    )
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Agent stopped by user.")


if __name__ == "__main__":
    if "--api" in sys.argv:
        # Start API server with Gmail polling running as a background task
        import uvicorn
        from api.app import create_app
        logger.info(f"Starting API server on {config.API_HOST}:{config.API_PORT}")
        uvicorn.run(create_app(), host=config.API_HOST, port=config.API_PORT)
    else:
        main()
