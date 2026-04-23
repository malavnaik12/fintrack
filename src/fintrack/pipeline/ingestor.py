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

        TODO:
        - Open the PDF with pdfplumber
        - Iterate over pdf.pages
        - Call extract_text() on each page, defaulting to "" if it returns None
        - Join all pages with a newline and return the result
        """
        with pdfplumber.open(file_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
        return "\n".join(pages_text)


class Ingestor:
    """
    Orchestrates the full ingestion flow for a single PDF statement.
    """

    def __init__(self, db_path: str):
        # TODO:
        # - Create an engine using get_engine(db_path)
        # - Call init_db to ensure tables exist
        # - Instantiate a DocumentParser and store it on self
        pass

    def _compute_hash(self, file_path: str) -> str:
        """
        Return the SHA-256 hex digest of the file at file_path.

        TODO:
        - Read the file as bytes
        - Return hashlib.sha256(<bytes>).hexdigest()
        """
        pass

    def ingest(
        self,
        file_path: str,
        document_type: DocumentType,
        institution: str,
        statement_period_start: date,
        statement_period_end: date,
    ) -> RawDocument | None:
        """
        Full ingestion flow for one PDF. Returns the RawDocument on success,
        or None if the file was already ingested (duplicate hash).

        TODO:
        - Compute the file hash
        - Check if a row already exists in raw_documents with that hash
          (hint: use row_exists — if it does, print a skip message and return None)
        - Extract text from the PDF using self.parser
        - Build a RawDocument model (don't set parsed=True yet — that's the normalizer's job)
        - Insert it into the DB
        - Return the RawDocument
        """
        pass
