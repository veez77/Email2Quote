import logging
import time

import schedule

import config
from gmail_client import GmailClient
from llm_client import LLMClient
from freight_parser import process_email
from priority1_client import Priority1Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


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
            freight_request = process_email(gmail, llm, message)

            if freight_request:
                print(freight_request.summary())

                # Placeholder: submit to Priority1
                quote_result = priority1.get_quote(freight_request)
                logger.info(f"Priority1 result: {quote_result}")

            # Mark as read regardless of parsing success to avoid reprocessing
            gmail.mark_as_read(message_id)

        except Exception as e:
            logger.error(f"Error processing email {message_id}: {e}", exc_info=True)


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

    # Schedule periodic checks
    schedule.every(config.POLL_INTERVAL_MINUTES).minutes.do(
        check_inbox, gmail, llm, priority1
    )

    logger.info(f"Polling every {config.POLL_INTERVAL_MINUTES} minutes. Press Ctrl+C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Agent stopped by user.")


if __name__ == "__main__":
    main()
