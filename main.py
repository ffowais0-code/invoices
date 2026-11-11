from fastapi import FastAPI, UploadFile, File, HTTPException
import pdfplumber
import json
import io
import os
import logging
import gspread
from groq import Groq
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timezone

load_dotenv()

# ---------- Logging (Bug A fix) ----------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("invoice-api")

# ---------- Config ----------
API_KEY = os.getenv("GROQ_API_KEY")
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Invoice_Data")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

if not API_KEY:
    raise RuntimeError("GROQ_API_KEY missing in .env")

client = Groq(api_key=API_KEY)

# Google Sheets Setup
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds_json = os.getenv("GOOGLE_CREDENTIALS")

if not creds_json:
    raise ValueError("GOOGLE_CREDENTIALS environment variable is missing!")

creds_dict = json.loads(creds_json)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gs_client = gspread.authorize(creds)

app = FastAPI(title="Invoice Extraction API", version="1.0.0")

# Single source of truth for row layout (Bug D fix)
ROW_COLUMNS = [
    "vendor_name", "invoice_number", "invoice_date", "due_date",
    "total_amount", "currency", "status", "filename",
    "confidence_score", "processed_at",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.get("/health")
def health():
    return {"status": "ok", "time": _now_utc()}


@app.post("/extract")
async def extract_invoice(file: UploadFile = File(...)):
    # Bug 1 fix: filename None safety
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        # Bug 2 fix: empty + size check
        contents = await file.read()
        if not contents:
            raise HTTPException(400, "Empty file")
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(413, "File too large (max 10 MB)")

        # 1. Extract text from ALL pages
        text = ""
        try:
            with pdfplumber.open(io.BytesIO(contents)) as pdf:
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
        except Exception:
            logger.exception("PDF parsing failed for %s", file.filename)
            raise HTTPException(422, "Could not read PDF")

        # Bug C fix: check OUTSIDE loop
        if not text.strip():
            raise HTTPException(422, "PDF has no extractable text (may be scanned image)")

        # 2. LLM extraction
        prompt = f"""
        Extract the following details from this invoice text. Return ONLY a valid JSON object.
        Fields: vendor_name, invoice_number, invoice_date, due_date, total_amount, currency, confidence_score (0 to 1).
        If any field is not found, use null.

        Invoice Text:
        {text}
        """

        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
        except Exception:
            logger.exception("LLM call failed for %s", file.filename)
            raise HTTPException(502, "Extraction service unavailable")

        # Bug B fix: single json.loads inside try
        try:
            data = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError:
            logger.error("LLM returned invalid JSON for %s", file.filename)
            raise HTTPException(502, "LLM returned invalid response")

        # 3. Open spreadsheet + tabs
        try:
            spreadsheet = gs_client.open(SHEET_NAME)
            valid_sheet = spreadsheet.worksheet("Valid")
            review_sheet = spreadsheet.worksheet("Needs Review")
            log_sheet = spreadsheet.worksheet("Processing_Log")
        except gspread.SpreadsheetNotFound:
            logger.error("Spreadsheet %r not found or not shared", SHEET_NAME)
            raise HTTPException(500, "Backend sheet not configured. Contact support.")
        except gspread.WorksheetNotFound as e:
            logger.error("Missing worksheet: %s", e)
            raise HTTPException(500, "Backend sheet is missing required tabs")

        # 4. Duplicate check across BOTH tabs, skip header
        invoice_num = (data.get("invoice_number") or "").strip()
        existing_invoices = set()
        for sheet in (valid_sheet, review_sheet):
            values = sheet.col_values(2)
            existing_invoices.update(v.strip() for v in values[1:] if v.strip())

        if invoice_num and invoice_num in existing_invoices:
            log_sheet.append_row([
                _now_utc(),
                file.filename,
                "Duplicate",
                invoice_num,
                "Invoice already exists in Valid or Needs Review tab",  # Bug E fix
            ])
            logger.info("Duplicate invoice %s (%s)", invoice_num, file.filename)
            return {
                "status": "duplicate",
                "filename": file.filename,
                "invoice_number": invoice_num,
                "message": "Invoice already processed",
            }

        # 5. Build row
        try:
            confidence = float(data.get("confidence_score") or 0)
        except (TypeError, ValueError):
            confidence = 0.0

        status = "Processed" if confidence >= 0.85 else "Needs Review"
        row = [
            data.get("vendor_name") or "",
            invoice_num,
            data.get("invoice_date") or "",
            data.get("due_date") or "",
            data.get("total_amount") or "",
            data.get("currency") or "",
            status,
            file.filename,
            confidence,
            _now_utc(),
        ]
        assert len(row) == len(ROW_COLUMNS), "Row shape mismatch"

        # 6. Route + log
        if confidence >= 0.85:
            valid_sheet.append_row(row)
            log_status = "Success"
            log_msg = "Added to Valid tab"
        else:
            review_sheet.append_row(row)
            log_status = "Needs Review"
            log_msg = "Added to Needs Review tab (Low Confidence)"

        log_sheet.append_row([
            _now_utc(),
            file.filename,
            log_status,
            invoice_num,
            log_msg,
        ])

        logger.info("Extraction done | file=%s invoice=%s confidence=%.2f",
                    file.filename, invoice_num, confidence)
        return {"status": "success", "filename": file.filename, "data": data}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled error while processing %s", file.filename)
        raise HTTPException(500, "Processing failed. Contact support.")