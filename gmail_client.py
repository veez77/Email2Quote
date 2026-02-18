import os
import base64
import logging
import tempfile

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)


class GmailClient:
    def __init__(self):
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Gmail API using OAuth2."""
        creds = None

        if os.path.exists(config.GMAIL_TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(
                config.GMAIL_TOKEN_FILE, config.GMAIL_SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(config.GMAIL_CREDENTIALS_FILE):
                    raise FileNotFoundError(
                        f"'{config.GMAIL_CREDENTIALS_FILE}' not found. "
                        "Download it from Google Cloud Console → APIs & Services → Credentials."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    config.GMAIL_CREDENTIALS_FILE, config.GMAIL_SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(config.GMAIL_TOKEN_FILE, "w") as token_file:
                token_file.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail API authenticated successfully.")

    def get_unread_emails(self, subject_filter: str) -> list[dict]:
        """Fetch unread emails matching the given subject filter."""
        query = f'is:unread subject:"{subject_filter}"'
        logger.info(f"Searching for emails with query: {query}")

        results = self.service.users().messages().list(
            userId="me", q=query
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            logger.info("No matching unread emails found.")
            return []

        logger.info(f"Found {len(messages)} matching email(s).")

        full_messages = []
        for msg in messages:
            full_msg = self.service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            full_messages.append(full_msg)

        return full_messages

    def get_email_body(self, message: dict) -> str:
        """Extract plain text body from a Gmail message."""
        payload = message.get("payload", {})
        return self._extract_text(payload)

    def _extract_text(self, payload: dict) -> str:
        """Recursively extract plain text from message payload."""
        mime_type = payload.get("mimeType", "")

        # Direct plain text part
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Multipart message — recurse into parts
        parts = payload.get("parts", [])
        for part in parts:
            # Prefer plain text
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Fallback: try HTML if no plain text found
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Nested multipart
        for part in parts:
            if "parts" in part:
                text = self._extract_text(part)
                if text:
                    return text

        return ""

    def mark_as_read(self, message_id: str):
        """Mark an email as read by removing the UNREAD label."""
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        logger.info(f"Marked message {message_id} as read.")

    def get_email_subject(self, message: dict) -> str:
        """Extract the subject from a Gmail message."""
        headers = message.get("payload", {}).get("headers", [])
        for header in headers:
            if header["name"].lower() == "subject":
                return header["value"]
        return ""

    def get_email_sender(self, message: dict) -> str:
        """Extract the sender from a Gmail message."""
        headers = message.get("payload", {}).get("headers", [])
        for header in headers:
            if header["name"].lower() == "from":
                return header["value"]
        return ""

    def get_attachments(self, message: dict) -> list[dict]:
        """Extract attachment metadata from a Gmail message.

        Returns a list of dicts with keys: filename, mime_type, attachment_id, size
        """
        attachments = []
        payload = message.get("payload", {})
        self._find_attachments(payload, message["id"], attachments)
        return attachments

    def _find_attachments(self, payload: dict, message_id: str, attachments: list):
        """Recursively find attachments in message payload."""
        filename = payload.get("filename", "")
        body = payload.get("body", {})
        attachment_id = body.get("attachmentId")

        if filename and attachment_id:
            attachments.append({
                "filename": filename,
                "mime_type": payload.get("mimeType", ""),
                "attachment_id": attachment_id,
                "message_id": message_id,
                "size": body.get("size", 0),
            })

        for part in payload.get("parts", []):
            self._find_attachments(part, message_id, attachments)

    def download_attachment(self, message_id: str, attachment_id: str, filename: str) -> str:
        """Download an attachment and save it to a temp file. Returns the file path."""
        attachment = self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()

        file_data = base64.urlsafe_b64decode(attachment["data"])

        # Save to temp directory
        temp_dir = tempfile.mkdtemp(prefix="email2quote_")
        file_path = os.path.join(temp_dir, filename)
        with open(file_path, "wb") as f:
            f.write(file_data)

        logger.info(f"Downloaded attachment '{filename}' ({len(file_data)} bytes) to {file_path}")
        return file_path
