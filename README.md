# Email2Quote

An AI agent that monitors a Gmail inbox for freight quote requests, extracts Bill of Lading (BOL) attachments, parses shipment details using Groq LLM, and fetches real carrier rates from Priority1.

## How It Works

```
Gmail Inbox
    └── Polls every N minutes for unread emails matching subject filter
            └── Extracts email body + BOL PDF attachment
                    └── Sends to Groq LLM (llama-3.3-70b)
                            └── Returns structured freight details
                                    └── POST /v2/ltl/quotes/rates → Priority1 carrier rates
```

Also exposes a **REST API** so Odoo ERP (or any external system) can submit a BOL PDF or plain text description and get quotes directly.

1. The agent polls Gmail for unread emails with subject containing **"BiziShip new Quotes request"**
2. For each matching email, it downloads any PDF attachments (BOL documents)
3. Text is extracted from the PDF and combined with the email body
4. The Groq LLM parses both sources and returns structured freight data:
   - Origin / Destination (city, state, zip)
   - Cargo description, weight, dimensions, piece count
   - Freight class, special requirements (hazmat, liftgate, etc.), pickup date
5. Freight details are submitted to **Priority1 LTL API** to get carrier quotes
6. Results are printed to the console
7. The email is marked as read to prevent reprocessing

## Project Structure

```
Email2Quote/
├── main.py               # Entry point — polling loop or API server (--api flag)
├── config.py             # Configuration loaded from .env
├── gmail_client.py       # Gmail API: auth, fetch, download attachments, mark read
├── llm_client.py         # Groq LLM integration for freight detail extraction
├── freight_parser.py     # FreightRequest data model + email processing orchestrator
├── priority1_client.py   # Priority1 LTL quoting API integration
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
├── .gitignore
└── api/                  # REST API (FastAPI)
    ├── app.py            # App factory + lifespan (Gmail polling as background task)
    ├── models.py         # Pydantic request/response models
    ├── dependencies.py   # API key auth
    └── routes/
        ├── health.py     # GET /health
        └── quote.py      # POST /quote/bol, POST /quote/text
```

## Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/veez77/Email2Quote.git
cd Email2Quote
pip install -r requirements.txt
```

### 2. Configure Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **Gmail API**
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
4. Choose **Desktop app**, download the JSON file
5. Save it as `credentials.json` in the project root
6. Go to **APIs & Services → OAuth consent screen → Test users** and add your Gmail address

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```env
GROQ_API_KEY=your-groq-api-key            # console.groq.com
PRIORITY1_API_KEY=your-priority1-api-key  # dev-dashboard.priority1.com
PRIORITY1_API_URL=https://dev-api.priority1.com
API_KEY=your-secret-key-for-rest-api
POLL_INTERVAL_MINUTES=5
```

### 4. Run

```bash
# Polling only (no HTTP server)
python main.py

# API server + Gmail polling together
python main.py --api
```

On first run, a browser window opens to authorize Gmail access. After that, `token.json` is saved and runs are fully automatic.

Interactive API docs available at **http://localhost:8000/docs** when running with `--api`.

## Priority1 Integration

Calls `POST /v2/ltl/quotes/rates` on the Priority1 API.

**Accessorial service mappings:**

| Our field | Priority1 code |
|---|---|
| `liftgate` / `liftgate_pickup` | `LGPU` |
| `liftgate_delivery` | `LGDEL` |
| `residential_delivery` | `RES` |
| `inside_delivery` | `IDEL` |
| `appointment` | `APPT` |
| `hazmat` | item flag `isHazardous: true` |

Switch to production by changing `PRIORITY1_API_URL=https://api.priority1.com` in `.env`.

## REST API Reference

| Endpoint | Method | Auth | Body | Returns |
|---|---|---|---|---|
| `/health` | GET | None | — | `{"status": "ok"}` |
| `/quote/bol` | POST | `X-API-Key` | PDF file (multipart) | `QuoteResponse` |
| `/quote/text` | POST | `X-API-Key` | Plain text | `QuoteResponse` |

**Odoo call example:**
```python
import requests
resp = requests.post(
    "http://your-server:8000/quote/bol",
    headers={"X-API-Key": "your-api-key"},
    files={"file": ("bol.pdf", open("bol.pdf", "rb"), "application/pdf")},
)
print(resp.json())
```

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | — | Required. [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `PRIORITY1_API_KEY` | — | Required. Priority1 API key |
| `PRIORITY1_API_URL` | `https://dev-api.priority1.com` | Use prod URL when ready |
| `POLL_INTERVAL_MINUTES` | `5` | Gmail check interval |
| `EMAIL_SUBJECT_FILTER` | `BiziShip new Quotes request` | Subject filter |
| `API_KEY` | — | Key for REST API `X-API-Key` header |
| `API_HOST` | `0.0.0.0` | REST API bind address |
| `API_PORT` | `8000` | REST API port |

## Roadmap

- [x] Gmail inbox monitoring
- [x] PDF BOL attachment extraction
- [x] LLM-based freight detail parsing (Groq)
- [x] Priority1 LTL carrier quoting API integration
- [x] REST API for Odoo / external integrations
- [ ] Support for scanned/image BOLs (OCR)
- [ ] Email reply with extracted quote details
- [ ] Support for additional mail providers (Outlook, etc.)
- [ ] Priority1 dispatch (create shipment after quote approval)
