import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", os.getenv("XAI_API_KEY"))
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))
EMAIL_SUBJECT_FILTER = os.getenv("EMAIL_SUBJECT_FILTER", "BiziShip new Quotes request")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
GMAIL_CREDENTIALS_FILE = "credentials.json"
GMAIL_TOKEN_FILE = "token.json"
