"""
pipeline/normalizer.py

Step 2 of the FinTrack pipeline.
Responsibilities:
  - Accept a RawDocument and its extracted text
  - Send the text to the LLM with a structured prompt
  - Parse the LLM's JSON response into NormalizedTransaction objects
  - Persist each transaction via TransactionStore (Step 3 — injected as dependency)
  - Flip RawDocument.parsed = True on completion
"""

import json
import logging
from datetime import date
from typing import Any

from langchain_ollama import OllamaLLM

from fintrack.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from fintrack.core.db import (
    get_engine,
    init_db,
    insert_row,
    raw_documents,
    normalized_transactions,
)
from fintrack.core.models import (
    DocumentType,
    NormalizedTransaction,
    RawDocument,
    TransactionType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
# Keep the prompt here as a module-level constant so it's easy to iterate on
# without touching logic code.
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """
You are a financial data extraction assistant.
Given the raw text of a bank statement, extract every transaction and return
them as a JSON array. Return ONLY the JSON array — no explanation, no markdown.

Each transaction object must have exactly these fields:
  - date:              "YYYY-MM-DD"
  - description_raw:   verbatim text from the statement
  - description_clean: human-readable version of the description
  - merchant_name:     best-guess merchant name, or null if unknown
  - amount:            positive float
  - transaction_type:  "debit" or "credit"

Raw statement text:
{raw_text}
"""


# ---------------------------------------------------------------------------
# TransactionExtractor
# ---------------------------------------------------------------------------


class TransactionExtractor:
    """
    Wraps the LLM call and JSON parsing.
    Kept separate from Normalizer so it can be mocked cleanly in tests.
    """

    def __init__(self):
        """
        Instantiate the OllamaLLM using base_url and model from config.
        Store it on self.llm.
        """
        self.llm = OllamaLLM(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            num_predict=1024,  # to try: -2
            temperature=0,
        )

    def build_prompt(self, raw_text: str) -> str:
        """
        Inject raw_text into EXTRACTION_PROMPT and return the filled string.

        Args:
            raw_text: The full text extracted from the PDF.

        Returns:
            The prompt string ready to send to the LLM.
        """
        _raw_filtered_text = raw_text.split("Transactions since your last statement")[
            1:
        ]
        return EXTRACTION_PROMPT.format(raw_text=_raw_filtered_text)

    def call_llm(self, prompt: str) -> str:
        """
        Send the prompt to the LLM and return the raw string response.

        Args:
            prompt: The fully formatted prompt string.

        Returns:
            Raw string response from the LLM.
        """
        return self.llm.invoke(prompt)

    def parse_response(self, response: str) -> list[dict[str, Any]]:
        """
        Parse the LLM's string response into a list of transaction dicts.

        The LLM should return a JSON array, but may occasionally wrap it in
        markdown fences (```json ... ```) — strip those before parsing.

        Args:
            response: Raw string returned by the LLM.

        Returns:
            List of dicts, each representing one raw transaction.

        Raises:
            ValueError: If the response cannot be parsed as a JSON array.
        """
        print(response)
        exit(1)
        cleaned = response.strip()
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"LLM response could not be parsed as JSON: {e}\nRaw response: {cleaned[:200]}"
            )

        if not isinstance(result, list):
            raise ValueError(
                f"Expected a JSON array, got {type(result).__name__}: {cleaned[:200]}"
            )
        return json.loads(response)

    def extract(self, raw_text: str) -> list[dict[str, Any]]:
        """
        Full extraction flow: build prompt → call LLM → parse response.

        Args:
            raw_text: The full text extracted from the PDF.

        Returns:
            List of raw transaction dicts from the LLM.
        """
        prompt = self.build_prompt(raw_text)
        response = self.call_llm(prompt)
        return self.parse_response(response)


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class Normalizer:
    """
    Orchestrates Step 2 of the pipeline.
    Converts a RawDocument's extracted text into persisted NormalizedTransactions
    and marks the document as parsed.
    """

    def __init__(self, db_path: str):
        """
        Set up the DB engine and a TransactionExtractor.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.engine = get_engine(db_path)
        init_db(self.engine)
        self.extractor = TransactionExtractor()

    def _build_transaction(
        self, raw: dict[str, Any], raw_document_id: str
    ) -> NormalizedTransaction:
        """
        Convert one raw dict from the LLM into a NormalizedTransaction model.

        Args:
            raw:             A single transaction dict from parse_response().
            raw_document_id: The ID of the parent RawDocument (provenance FK).

        Returns:
            A validated NormalizedTransaction instance.
        """
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

    def normalize(
        self, raw_document: RawDocument, raw_text: str
    ) -> list[NormalizedTransaction]:
        """
        Full normalization flow for one document.

        Extracts transactions from raw_text, persists each one, marks the
        RawDocument as parsed, and returns the list of NormalizedTransactions.

        Args:
            raw_document: The RawDocument produced by the Ingestor.
            raw_text:     The full text extracted from the PDF by DocumentParser.

        Returns:
            List of persisted NormalizedTransaction objects.
        """
        from sqlalchemy import update

        raw_list = self.extractor.extract(raw_text)
        transactions = [self._build_transaction(r, raw_document.id) for r in raw_list]

        with self.engine.connect() as conn:
            for txn in transactions:
                insert_row(conn, normalized_transactions, txn.model_dump())

            conn.execute(
                update(raw_documents)
                .where(raw_documents.c.id == raw_document.id)
                .values(parsed=True)
            )
            conn.commit()

        logger.info(
            f"[normalizer] {len(transactions)} transactions extracted from document {raw_document.id}"
        )
        return transactions


if __name__ == "__main__":
    from fintrack.core.config import DB_PATH
    from fintrack.core.db import fetch_all, get_engine, normalized_transactions
    from fintrack.pipeline.ingestor import DocumentParser, Ingestor
    from fintrack.core.models import DocumentType

    PDF_PATH = (
        r"C:\Users\malav\Desktop\fintrack\Statements_for_Budgetizer\April 13, 2026.pdf"
    )
    INSTITUTION = "TD"

    # Step 1 — ingest
    ingestor = Ingestor(db_path=DB_PATH)
    raw_doc = ingestor.ingest(
        file_path=PDF_PATH,
        document_type=DocumentType.CREDIT,
        institution=INSTITUTION,
        statement_period_start=date(2026, 3, 13),
        statement_period_end=date(2026, 4, 13),
    )

    if raw_doc is None:
        print("Already ingested — delete the DB and retry")
    else:
        # Step 2 — extract text
        raw_text = DocumentParser().extract_text(PDF_PATH)
        # print(f"Extracted {raw_text}")

        # Step 3 — normalize
        normalizer = Normalizer(db_path=DB_PATH)
        transactions = normalizer.normalize(raw_doc, raw_text)
        print(f"{len(transactions)} transactions extracted")

        # Step 4 — inspect DB
        with get_engine(DB_PATH).connect() as conn:
            rows = fetch_all(conn, normalized_transactions)

        for row in rows:
            print(
                f"{row['date']}  {row['transaction_type']:<6}  ${row['amount']:>8.2f}  {row['description_clean']}"
            )
