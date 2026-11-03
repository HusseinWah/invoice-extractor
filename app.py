import io
import time
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from pypdf import PdfReader

load_dotenv(".env", override=True)
client = genai.Client(http_options=types.HttpOptions(timeout=60_000))
MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash"]


class Invoice(BaseModel):
    invoice_number: Optional[str] = None
    supplier: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="Format YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="Format YYYY-MM-DD")
    total: Optional[float] = Field(None, description="Final total as a plain number")
    currency: Optional[str] = Field(None, description="3-letter code like EGP or USD")


RULES = """Extract the invoice fields.
Rules:
- Only use values that are printed on the invoice.
- If a field is not printed, return null. Never calculate or guess it.
- Payment terms like "Net 30" are NOT a due date."""

SUPPLIERS = [
    "Delta Paper & Print Co.", "Cairo Cable Works", "Pyramids Furniture Factory",
    "Alex Packaging Solutions", "Sinai Steel Fixtures", "Horus IT Services",
    "Zamalek Cleaning Services", "Red Sea Logistics",
]


def match_supplier(name):
    if name is None:
        return None
    for s in SUPPLIERS:
        if s.lower() == name.strip().lower():
            return s
    return name


def extract(pdf_bytes):
    text = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
    if text.strip() == "":
        # Scanned invoice: send the PDF itself so Gemini can look at it
        contents = [types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), RULES]
    else:
        contents = f"{RULES}\n\nInvoice text:\n{text}"
    for model in MODELS * 2:
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config={"response_mime_type": "application/json", "response_schema": Invoice},
            )
            print(f"OK with {model}")
            return response.parsed
        except Exception as e:
            print(f"{model} failed: {str(e)[:150]}")
            time.sleep(10)
    return None

st.title("Invoice Extractor")
st.write("Upload supplier invoices (PDF). Each invoice becomes one row you can download as Excel.")

files = st.file_uploader("Invoices", type="pdf", accept_multiple_files=True)

if files and st.button("Extract"):
    rows = []
    progress = st.progress(0.0)
    for i, f in enumerate(files):
        inv = extract(f.read())
        if inv is None:
            st.warning(f"{f.name}: the AI service is busy. Try this one again in a minute.")
        else:
            row = {"file": f.name}
            row.update(inv.model_dump())
            row["supplier"] = match_supplier(row["supplier"])
            row["needs_review"] = any(v is None for v in inv.model_dump().values())
            rows.append(row)
        progress.progress((i + 1) / len(files))
        time.sleep(2)
    st.session_state["df"] = pd.DataFrame(rows)

if "df" in st.session_state and not st.session_state["df"].empty:
    df = st.session_state["df"]
    st.dataframe(df, hide_index=True)
    flagged = int(df["needs_review"].sum())
    if flagged:
        st.info(f"{flagged} invoice(s) have empty fields. A person should check them before payment.")
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    st.download_button("Download Excel", buffer.getvalue(), file_name="extracted_invoices.xlsx")
