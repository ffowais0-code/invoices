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

# Render par hum credentials.json ko direct file ki tarah use karenge
creds_json = os.getenv("GOOGLE_CREDENTIALS")
if not creds_json:
    raise ValueError("GOOGLE_CREDENTIALS environment variable is missing!")
creds_dict = json.loads(creds_json)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gs_client = gspread.authorize(creds)
sheet = gs_client.open(SHEET_NAME).sheet1

@app.post("/extract")
async def extract_invoice(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        # PDF file ko memory mein read karein
        contents = await file.read()
        text = ""
        
        # pdfplumber memory se file padhta hai
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""

        prompt = f"""
        Extract the following details from this invoice text. Return ONLY a valid JSON object.
        Fields: vendor_name, invoice_number, invoice_date, due_date, total_amount, currency, confidence_score (0 to 1).
        If any field is not found, use null.
        
        Invoice Text:
        {text}
        """
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role" : "user", "content" : prompt}],
            response_format={"type":"json_object"}
        )
        
        data = json.loads(response.choices[0].message.content)
        
        # Data Google Sheet mein save karein
        row = [
            data.get("vendor_name", ""),
            data.get("invoice_number", ""),
            data.get("invoice_date", ""),
            data.get("due_date", ""),
            data.get("total_amount", ""),
            data.get("currency", ""),
            "Processed" if data.get("confidence_score", 0) > 0.85 else "Needs Review",
            file.filename,
            data.get("confidence_score", 0),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
        sheet.append_row(row)
        
        return {"status": "success", "filename": file.filename, "data": data}

    except Exception as e:
        return {"status": "error", "message": str(e)}