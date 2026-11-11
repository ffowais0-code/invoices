from fastapi.testclient import TestClient
from main import app
import pytest
from unittest.mock import patch, MagicMock

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_upload_non_pdf():
    file_content = b"Hello, this is not a pdf"
    response = client.post(
        "/extract",
        files={"file": ("test.txt", file_content, "text/plain")}
    )
    assert response.status_code == 400
    assert "Only PDF files are allowed" in response.json()["detail"]

def test_upload_empty_pdf():
    response = client.post(
        "/extract",
        files={"file": ("empty.pdf", b"", "application/pdf")}
    )
    assert response.status_code == 400
    assert "Empty file" in response.json()["detail"]

# Yahan humne 3 cheezon ko mock kiya hai: Groq LLM, PDF Reader, aur Google Sheets
@patch("main.client.chat.completions.create")
@patch("pdfplumber.open")
@patch("main.gs_client")
def test_successful_extraction(mock_gs_client, mock_pdf_open, mock_groq):
    
    # 1. Mock Groq API Response
    mock_groq.return_value.choices = [
        MagicMock(message=MagicMock(content='{"vendor_name": "Test Vendor", "invoice_number": "INV-123", "confidence_score": 0.95}'))
    ]

    # 2. Mock PDF Reader (Fix for the error)
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Invoice Text"
    
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf_open.return_value.__enter__.return_value = mock_pdf

    # 3. Mock Google Sheets (Taake test real Google Sheet se na takraye)
    mock_valid_sheet = MagicMock()
    mock_valid_sheet.col_values.return_value = ["Invoice Number", "INV-000"] # Dummy data
    
    mock_review_sheet = MagicMock()
    mock_review_sheet.col_values.return_value = ["Invoice Number"]
    
    mock_log_sheet = MagicMock()
    
    mock_spreadsheet = MagicMock()
    # Jab code sheet.worksheet("Valid") call kare, toh usko mock object de
    mock_spreadsheet.worksheet.side_effect = lambda name: {
        "Valid": mock_valid_sheet,
        "Needs Review": mock_review_sheet,
        "Processing_Log": mock_log_sheet
    }[name]
    
    mock_gs_client.open.return_value = mock_spreadsheet

    # 4. Send Request
    response = client.post(
        "/extract",
        files={"file": ("test.pdf", b"fake pdf content", "application/pdf")}
    )

    # 5. Assertions
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["data"]["vendor_name"] == "Test Vendor"