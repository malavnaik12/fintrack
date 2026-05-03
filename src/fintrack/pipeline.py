"""
pipeline.py

Single-file pipeline for FinTrack.
Ingests a PDF statement, extracts transactions via LLM, and persists them to the DB.
"""

import hashlib
import pdfplumber
from pathlib import Path
from datetime import date
from fintrack.core.config import DB_PATH
from fintrack.core.db import get_engine, init_db, row_exists, raw_documents, insert_row
from fintrack.core.models import DocumentType, RawDocument

# DB setup — runs once when the module loads
engine = get_engine(DB_PATH)
init_db(engine)


BMO_TRANSACTION_MARKER = "Transactions since your last statement"


def extract_pdf_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        pages_text = []
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")
        return "\n".join(pages_text)


def ingestor(
    pdf_path: str,
    institution: str,
    document_type: DocumentType,
    statement_period_start: date,
    statement_period_end: date,
) -> RawDocument | None:
    raw_text = extract_pdf_text(pdf_path=pdf_path)
    _file_hash = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    with engine.connect() as conn:
        if row_exists(conn, raw_documents, "file_hash", _file_hash):
            print(f"[ingestor] skipping {pdf_path} — already ingested")
            return None

        doc = RawDocument(
            file_path=str(pdf_path),
            file_hash=_file_hash,
            document_type=document_type,
            institution=institution,
            statement_period_start=statement_period_start,
            statement_period_end=statement_period_end,
        )

        insert_row(conn, raw_documents, doc.model_dump())
    return doc


if __name__ == "__main__":
    raw_doc = ingestor(
        pdf_path="data/PDFs/April 13, 2026.pdf",
        institution="BMO",
        document_type=DocumentType.CREDIT,
        statement_period_start=date(2026, 3, 14),
        statement_period_end=date(2024, 4, 13),
    )
    print(raw_doc)
