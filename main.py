from fastapi import FastAPI, UploadFile, File, HTTPException
import pdfplumber
import json
import io
import os
import gspread
from groq import Groq
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

load_dotenv()

app = FastAPI(title="Invoice Extraction API")

API_KEY = os.getenv("GROQ_API_KEY")
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Invoice_Data")
client = Groq(api_key=API_KEY)

# Google Sheets Setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = os.getenv("GOOGLE_CREDENTIALS")

if not creds_json:
    raise ValueError("GOOGLE_CREDENTIALS environment variable is missing!")
    
creds_dict = json.loads(creds_json)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gs_client = gspread.authorize(creds)

@app.post("/extract")
async def extract_invoice(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        # 1. PDF Read Karein
        contents = await file.read()
        text = ""
        
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""

        # 2. Groq LLM se Data Extract Karwayein
        prompt = f"""
        Extract the following details from this invoice text. Return ONLY a valid JSON object.
        Fields: vendor_name, invoice_number, invoice_date, due_date, total_amount, currency, confidence_score (0 to 1).
        If any field is not found, use null.
        
        Invoice Text:
        {text}
        """
        
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role" : "user", "content" : prompt}],
            response_format={"type":"json_object"}
        )
        
        data = json.loads(response.choices[0].message.content)
        
        # 3. Google Sheets aur Tabs Open Karein
        spreadsheet = gs_client.open(SHEET_NAME)
        valid_sheet = spreadsheet.worksheet("Valid")
        review_sheet = spreadsheet.worksheet("Needs Review")
        log_sheet = spreadsheet.worksheet("Processing_Log")

        # 4. Duplicate Check (Invoice Number column B mein check karein)
        invoice_num = data.get("invoice_number", "")
        existing_invoices = valid_sheet.col_values(2) # Column B
        
        if invoice_num and invoice_num in existing_invoices:
            # Log mein Duplicate Likh Dein
            log_sheet.append_row([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                file.filename, 
                "Duplicate", 
                invoice_num, 
                "Invoice already exists in Valid tab"
            ])
            return {"status": "duplicate", "filename": file.filename, "message": "Invoice already processed"}

        # 5. Data ki Row Tayyar Karein
        confidence = data.get("confidence_score", 0)
        row = [
            data.get("vendor_name", ""),
            invoice_num,
            data.get("invoice_date", ""),
            data.get("due_date", ""),
            data.get("total_amount", ""),
            data.get("currency", ""),
            "Processed" if confidence >= 0.85 else "Needs Review",
            file.filename,
            confidence,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]

        # 6. Routing (Confidence ke hisaab se tab mein data daalein)
        if confidence >= 0.85:
            valid_sheet.append_row(row)
            log_status = "Success"
            log_msg = "Added to Valid tab"
        else:
            review_sheet.append_row(row)
            log_status = "Needs Review"
            log_msg = "Added to Needs Review tab (Low Confidence)"

        # 7. Processing Log Mein Entry Karein
        log_sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
            file.filename, 
            log_status, 
            invoice_num, 
            log_msg
        ])
        
        return {"status": "success", "filename": file.filename, "data": data}

    except Exception as e:
        return {"status": "error", "message": str(e)}