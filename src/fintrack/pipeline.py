"""
pipeline.py

Single-file pipeline for FinTrack.
Ingests a PDF statement, extracts transactions via LLM, and persists them to the DB.
"""

import json
import hashlib
import pdfplumber
from pathlib import Path
from datetime import date, datetime
from langchain_ollama import OllamaLLM
from fintrack.core.config import DB_PATH, OLLAMA_BASE_URL, OLLAMA_MODEL
from fintrack.core.db import (
    get_engine,
    init_db,
    row_exists,
    raw_documents,
    normalized_transactions,
    insert_row,
    fetch_row,
)
from fintrack.core.models import (
    DocumentType,
    RawDocument,
    NormalizedTransaction,
    TransactionType,
)
import re
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MONTH_MAP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

# Lines that are definitely headers / footers — skip them entirely
SKIP_RE = re.compile(
    r"^(TRANS\s+POSTING|DATE\s+DATE|Card number|Page \d|Subtotal|Total for"
    r"|®|^\*Trademark|BMO|Mr Malav|\(continued)",
    re.IGNORECASE,
)

# A transaction always starts with two dates: "Mon. DD Mon. DD"
TX_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.\s*(\d{1,2})"
    r"\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.\s*(\d{1,2})"
    r"\s+(.+?)\s+([\d,]+\.\d{2})\s*(CR)?\s*$",
    re.IGNORECASE,
)


def parse_date(month_abbr: str, day: str, year: int = 2025) -> str:
    return datetime(year, MONTH_MAP[month_abbr.capitalize()], int(day)).strftime(
        "%Y-%m-%d"
    )


def infer_base_year(pages: list[str]) -> int:
    combined = " ".join(pages)
    hits = re.findall(r"\b(20\d{2})\b", combined)
    if hits:
        year = int(hits[0])
        print(f"📅 Detected statement year: {year}")
        return year
    year = datetime.now().year
    print(
        f"⚠️  No year found in statement text — defaulting to current year ({year}). "
        f"Pass --year YYYY to override."
    )
    return year


def parse_statement(pages: list[str], year: int) -> list:
    """
    Parse raw statement page strings into a DataFrame.
    Handles multi-line descriptions by appending unrecognised lines
    to the current transaction's description.
    """
    transactions = []
    current = None

    for page in pages:
        for line in page.splitlines():
            line = line.strip()
            if not line:
                continue
            if SKIP_RE.search(line):
                continue

            m = TX_RE.match(line)
            if m:
                if current:
                    transactions.append(current)
                t_mon, t_day, p_mon, p_day, desc, amount, cr = m.groups()
                current = {
                    "posting_date": parse_date(p_mon, p_day, year),
                    "description_raw": desc.strip(),
                    "amount": float(amount.replace(",", "")),
                    "transaction_type": "credit" if cr else "debit",
                    "description_clean": None,
                    "merchant_name": None,
                }
            elif current:
                # Continuation line — append to description
                current["description_raw"] += " " + line

    if current:
        transactions.append(current)

    # df = pd.DataFrame(transactions)
    # print(
    #     f"✅ Parsed {len(df)} transactions ({(df.transaction_type == 'debit').sum()} debits, "
    #     f"{(df.transaction_type == 'credit').sum()} credits)"
    # )
    return transactions


# DB setup - runs once when the module loads
engine = get_engine(DB_PATH)
init_db(engine)

# LLM setup - runs once when the module loads
llm = OllamaLLM(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    num_predict=4096,  # to try: -2
    temperature=0,
)


BMO_TRANSACTION_MARKER = "Transactions since your last statement"
BMO_STATEMENT_PERIOD = "Statement period"
EXTRACTION_PROMPT = """You are a bank transaction classifier.
Given a raw transaction description from a credit card statement,
return ONLY a JSON object with exactly two fields:
  "merchant_name":     best-guess merchant name string, or null
  "description_clean": short human-readable description string

Rules for description_clean:
- Format is strictly "Category – Merchant" (em dash, no extra words)
- Category must be a single concise noun: Fuel, Groceries, Dining, Coffee,
  Transit, Parking, Streaming, Software, Clothing, Health, Entertainment,
  Transfer, Travel, Membership, or Other
- Merchant is the clean merchant name only, no location or extra detail
- Examples:
    "SHELL EASYPAY C81358 CALEDON EAST ON"  → "Fuel – Shell"
    "TIM HORTONS #0474 HORNBY ON"           → "Coffee – Tim Hortons"
    "EUREST-MACDONALDDET-23846 BRAMPTON ON" → "Dining – Eurest"
    "MICROSOFT*MICROSOFT 36 HALIFAX NS"     → "Software – Microsoft 365"
    "TRSF FROM/DE ACCT/CPT 3096-XXXX-916"  → "Transfer – BMO"
    "USD 5.65@1.405309734 ANTHROPIC"        → "Software – Anthropic"

No explanation. No markdown. No preamble. JSON only.

Raw statement text:
{raw_text}
"""


def _get_dt_statement_dates(raw_text_info: str) -> tuple:
    raw_statement_dates = (
        raw_text_info.split(BMO_STATEMENT_PERIOD)[-1].split("\n")[0].split("-")
    )
    dt_start = datetime.strptime(
        raw_statement_dates[0].replace(".", "").strip(), "%b %d, %Y"
    )
    dt_end = datetime.strptime(
        raw_statement_dates[1].replace(".", "").strip(), "%b %d, %Y"
    )

    return (dt_start.date(), dt_end.date())


def _build_transaction(
    raw: dict[str, any], raw_document_id: str
) -> NormalizedTransaction:
    return NormalizedTransaction(
        raw_document_id=raw_document_id,
        date=date.fromisoformat(raw["date"]),
        description_raw=raw["description_raw"],
        description_clean=raw["description_clean"],
        merchant_name=raw.get("merchant_name"),
        amount=float(raw["amount"]),
        transaction_type=TransactionType(raw["transaction_type"]),
        category=None,
        subcategory=None,
        is_recurring=None,
    )


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
) -> RawDocument:
    _file_hash = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    with engine.connect() as conn:
        _check = row_exists(conn, raw_documents, "file_hash", _file_hash)
        if _check:
            doc = fetch_row(conn, raw_documents, "file_hash", _file_hash)
            print(f"[ingestor] {doc.id} already ingested")
        else:
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


def normalize(
    raw_transactions: list, raw_document: RawDocument
) -> NormalizedTransaction:
    year = infer_base_year(raw_transactions)
    processed_transactions = parse_statement(pages=raw_transactions[1:], year=year)
    for txn in processed_transactions:
        # print(txn)
        _llm_prompt = EXTRACTION_PROMPT.format(raw_text=txn["description_raw"])
        # print(_llm_prompt)
        response = llm.invoke(_llm_prompt).strip()
        result = json.loads(response)
        print(f"[normalize] LLM Response: {result}")
    exit(1)
    try:
        result = json.loads(response)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM response could not be parsed as JSON: {e}\nRaw response: {response}"
        )

    from sqlalchemy import update

    transactions = [_build_transaction(r, raw_document.id) for r in result]

    with engine.connect() as conn:
        for txn in transactions:
            insert_row(conn, normalized_transactions, txn.model_dump())

        conn.execute(
            update(raw_documents)
            .where(raw_documents.c.id == raw_document.id)
            .values(parsed=True)
        )
        conn.commit()

    print(
        f"[normalize] {len(transactions)} transactions extracted from document {raw_document.file_path}"
    )


def main(pdf_path, institution: str, document_type: DocumentType):
    raw_text = extract_pdf_text(pdf_path=pdf_path)

    raw_text_split = raw_text.split(BMO_TRANSACTION_MARKER)

    dt_statement_start, dt_statement_end = _get_dt_statement_dates(raw_text_split[0])

    ingested_doc = ingestor(
        pdf_path=pdf_path,
        institution=institution,
        document_type=document_type,
        statement_period_start=dt_statement_start,
        statement_period_end=dt_statement_end,
    )
    print(f"[ingestor] {ingested_doc}")
    # print(f"[pipeline] transaction text preview:\n{raw_transactions[:300]}")

    _ = normalize(raw_transactions=raw_text_split, raw_document=ingested_doc)


if __name__ == "__main__":
    main(
        pdf_path="data/PDFs/April 13, 2026.pdf",
        institution="BMO",
        document_type=DocumentType.CREDIT,
    )
