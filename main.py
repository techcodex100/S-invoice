import os
import re
import tempfile
from typing import Dict, Any, List

import pdfplumber
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Sample Invoice Extractor API")


def extract_text_lines(path: str) -> List[str]:
    lines = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines.extend(text.splitlines())
    return [l.strip() for l in lines if l.strip()]


def clean_cell(cell):
    return cell.strip() if cell else ""


def extract_line_items(path: str) -> List[Dict[str, Any]]:
    """Extract line items (Sr. No., HS Code, Description, Qty, Rate, Amount)."""
    items = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or not row[0]:
                        continue
                    if row[0] and row[0].strip().isdigit():  # Sr. No.
                        items.append({
                            "sr_no": clean_cell(row[0]),
                            "hs_code": clean_cell(row[1]) if len(row) > 1 else "",
                            "description": clean_cell(row[2]) if len(row) > 2 else "",
                            "qty": clean_cell(row[3]) if len(row) > 3 else "",
                            "rate": clean_cell(row[4]) if len(row) > 4 else "",
                            "amount": clean_cell(row[5]) if len(row) > 5 else ""
                        })
    return items


def capture_block(lines: List[str], start_keywords: List[str], stop_keywords: List[str]) -> str:
    """Capture multi-line block between start and stop keywords."""
    block = []
    capture = False
    for line in lines:
        if any(re.search(rf"^{kw}", line, re.IGNORECASE) for kw in start_keywords):
            capture = True
            continue
        if capture:
            if any(re.search(rf"^{kw}", line, re.IGNORECASE) for kw in stop_keywords):
                break
            block.append(line)
    return "\n".join(block).strip()


def extract_fields(path: str) -> Dict[str, Any]:
    lines = extract_text_lines(path)
    data: Dict[str, Any] = {}

    # --- Exporter Block ---
    exporter_block = capture_block(lines, ["Exporter"], ["Invoice No", "Consignee"])
    if exporter_block:
        parts = exporter_block.split("\n")
        data["exporter_name"] = parts[0]
        data["exporter_address"] = " ".join(parts[1:]) if len(parts) > 1 else ""

    # --- Invoice No & Ref ---
    for i, l in enumerate(lines):
        if "Invoice No" in l:
            data["invoice_no_date"] = lines[i + 1] if i + 1 < len(lines) else ""
        if "REF" in l.upper():
            tokens = l.split()
            data["exporter_ref"] = tokens[-1]

    # --- Consignee Block ---
    consignee_block = capture_block(lines, ["Consignee"], ["Buyer", "Pre-carriage"])
    if consignee_block:
        data["consignee"] = consignee_block

    # --- Buyer Block ---
    buyer_block = capture_block(lines, ["Buyer"], ["Pre-carriage", "Port of Loading"])
    if buyer_block:
        data["buyer"] = buyer_block

    # --- Ports ---
    for i, l in enumerate(lines):
        if l.startswith("Pre-carriage"):
            data["pre_carriage"] = lines[i + 1] if i + 1 < len(lines) else ""
        if "Port of Loading" in l:
            data["port_of_loading"] = lines[i + 1] if i + 1 < len(lines) else ""
        if "Port of Discharge" in l:
            data["port_of_discharge"] = l.split()[-1]
        if "Final Destination" in l:
            data["final_destination"] = l.split()[-1]

    # --- Totals ---
    for l in lines:
        if "Net Weight" in l:
            data["net_weight"] = l.replace("Net Weight", "").strip()
        if "Gross Weight" in l:
            data["gross_weight"] = l.replace("Gross Weight", "").strip()
        if "Amount in words" in l:
            data["amount_in_words"] = l.split(":", 1)[-1].strip()
        if l.startswith("Total"):
            parts = l.split()
            data["total_amount"] = parts[-1]

    # --- Authorised Signature ---
    data["authorised_signature"] = "Yes" if any("Authorised Signature" in l for l in lines) else ""

    # --- Line Items ---
    data["line_items"] = extract_line_items(path)

    return data


@app.post("/extract_sample_invoice")
async def extract_sample_invoice(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        data = extract_fields(tmp_path)
        return JSONResponse(content={"success": True, "fields": data})
    finally:
        os.unlink(tmp_path)


@app.get("/")
def root():
    return {"info": "Upload a Sample Invoice PDF at /extract_sample_invoice"}
