# Invoice Extraction API

Upload a PDF invoice → get structured JSON → auto-filed into Google Sheets.
**Built with FastAPI, Groq (Llama 3 family), pdfplumber, and Google Sheets API.**

---

## What it does

- Accepts PDF invoices via `POST /extract`
- Uses Groq LLM to extract `vendor`, `invoice #`, dates, amount, currency
- **Auto-routes** to `Valid` tab if confidence ≥ 0.85, else `Needs Review`
- **Detects duplicates** across both tabs
- Logs every request to a `Processing_Log` sheet
- Clean HTTP error codes for every failure mode

## Why it exists

Small businesses copy invoice data from PDFs into spreadsheets by hand.
That's **5–10 minutes per invoice**. This API does it in **5 seconds**
and only asks for human review when it's unsure.

## Try it

1. Open **[Demo video on Loom]** (2 min)
2. Or open Swagger UI: `http://localhost:8000/docs`

## Tech stack

| Layer | Tool |
|-------|------|
| API | FastAPI |
| LLM | Groq (`openai/gpt-oss-120b`) |
| PDF parsing | pdfplumber |
| Storage | Google Sheets |
| Auth | Google Service Account |

## Local setup

```bash
# 1. Clone
git clone https://github.com/YOURNAME/invoice-extraction-api
cd invoice-extraction-api

# 2. Env
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Secrets
cp .env.example .env
# Fill GROQ_API_KEY and GOOGLE_CREDENTIALS in .env

# 4. Create Google Sheet named "Invoice_Data" with 3 tabs:
#    Valid | Needs Review | Processing_Log
#    Share with the service-account email from GOOGLE_CREDENTIALS

# 5. Run
uvicorn main:app --reload
```

## Example request

```bash
curl -X POST http://localhost:8000/extract \
     -F "file=@samples/sample_invoice_01.pdf"
```

```json
{
  "status": "success",
  "filename": "sample_invoice_01.pdf",
  "data": {
    "vendor_name": "Acme Supplies Ltd",
    "invoice_number": "INV-2024-0912",
    "invoice_date": "2024-09-12",
    "due_date": "2024-10-12",
    "total_amount": 1250.00,
    "currency": "USD",
    "confidence_score": 0.94
  }
}
```

## Error handling

| Code | Reason |
|------|--------|
| 400 | Not a PDF / empty file |
| 413 | File > 10 MB |
| 422 | PDF has no extractable text (scanned image) |
| 502 | LLM returned invalid response |
| 500 | Backend sheet / config error (client-safe message) |

## Design decisions

- **Duplicate detection checks both `Valid` and `Needs Review`** — same invoice
  cannot slip through the low-confidence path.
- **All unhandled exceptions** are logged with stack trace server-side,
  but the client only receives a generic 500 message. No internal leaks.
- **Row layout is declared in one place** (`ROW_COLUMNS`) so code and sheet
  can never silently drift apart.
- **Confidence is always coerced to float** — the LLM sometimes returns
  `"0.9"` as a string, which would crash numeric comparison without this guard.

## Roadmap

- [ ] Batch upload (multiple PDFs at once)
- [ ] Excel export from Valid tab
- [ ] Webhook on new invoice
- [ ] Retry queue for LLM failures

## Author

**Your Name** — [LinkedIn](https://linkedin.com/in/yourname) · your@email.com