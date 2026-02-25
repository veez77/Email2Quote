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

# Priority1 API settings
PRIORITY1_API_KEY = os.getenv("PRIORITY1_API_KEY")
PRIORITY1_API_URL = os.getenv("PRIORITY1_API_URL", "https://dev-api.priority1.com")

# REST API settings
API_KEY = os.getenv("API_KEY")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
