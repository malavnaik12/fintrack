"""
pipeline/ingestor.py

Step 1 of the FinTrack pipeline.
Responsibilities:
  - Accept a path to a PDF statement
  - Compute a SHA-256 hash of the file (dedup key)
  - Extract raw text from the PDF using pdfplumber
  - Build and persist a RawDocument record
  - Skip the file silently if it has already been ingested (same hash)
"""

import hashlib
from datetime import date
from pathlib import Path
import pdfplumber

from fintrack.core.db import get_engine, init_db, insert_row, row_exists, raw_documents
from fintrack.core.models import DocumentType, RawDocument


# ---------------------------------------------------------------------------
# pdfplumber primer
# ---------------------------------------------------------------------------
# Open a PDF:
#   with pdfplumber.open("path/to/file.pdf") as pdf:
#       pdf.pages        → list of Page objects
#       page.extract_text()  → string of all text on that page (can be None)
#
# Text extraction is imperfect — always guard against None returns.
# ---------------------------------------------------------------------------


class DocumentParser:
    """
    Extracts raw text from a PDF statement.
    Kept separate from ingestion so it can be tested independently.
    """

    def extract_text(self, file_path: str) -> str:
        """
        Open the PDF at file_path and return all pages joined as a single string.
        """
        with pdfplumber.open(file_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
        return "\n".join(pages_text)


class Ingestor:
    """
    Orchestrates the full ingestion flow for a single PDF statement.
    Owns the DB engine and delegates parsing to DocumentParser.
    """

    def __init__(self, db_path: str):
        """
        Initialise the Ingestor with a path to the SQLite database.
        Creates the engine, ensures all tables exist, and prepares a parser.

        Args:
            db_path: Absolute or relative path to the SQLite .db file.
        """
        self.engine = get_engine(db_path)
        init_db(self.engine)
        self.parser = DocumentParser()

    def _compute_hash(self, file_path: str) -> str:
        """
        Compute and return the SHA-256 hex digest of the file at file_path.
        Used as the dedup key — re-ingesting the same file is a no-op.

        Args:
            file_path: Path to the PDF file to hash.

        Returns:
            64-character lowercase hex string (SHA-256 digest).
        """
        return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()

    def ingest(
        self,
        file_path: str,
        document_type: DocumentType,
        institution: str,
        statement_period_start: date,
        statement_period_end: date,
    ) -> RawDocument | None:
        """
        Full ingestion flow for one PDF statement.

        Computes the file hash, skips silently if already ingested, otherwise
        extracts text, builds a RawDocument, persists it, and returns it.
        The parsed flag is left False — the normalizer flips it after Step 2.

        Args:
            file_path:               Absolute path to the PDF on disk.
            document_type:           CREDIT or DEBIT (DocumentType enum).
            institution:             Human-readable bank name e.g. "TD", "RBC".
            statement_period_start:  First date covered by the statement.
            statement_period_end:    Last date covered by the statement.

        Returns:
            The persisted RawDocument, or None if the file was already ingested.
        """
        file_hash = self._compute_hash(file_path)

        with self.engine.connect() as conn:
            if row_exists(conn, raw_documents, "file_hash", file_hash):
                print(f"[ingestor] skipping {file_path} — already ingested")
                return None

            doc = RawDocument(
                file_path=str(file_path),
                file_hash=file_hash,
                document_type=document_type,
                institution=institution,
                statement_period_start=statement_period_start,
                statement_period_end=statement_period_end,
            )

            insert_row(conn, raw_documents, doc.model_dump())
            return doc
