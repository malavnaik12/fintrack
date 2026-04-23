"""
tests/test_ingestor.py

Unit tests for pipeline/ingestor.py.
Covers DocumentParser and Ingestor independently.

Run from project root: pytest tests/test_ingestor.py -v
"""

import hashlib
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fintrack.core.models import DocumentType, RawDocument
from fintrack.pipeline.ingestor import DocumentParser, Ingestor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ingestor(db_path: str) -> Ingestor:
    """Convenience factory so tests don't repeat the constructor call."""
    return Ingestor(db_path=db_path)


def ingest_dummy(ingestor: Ingestor, file_path: str) -> RawDocument | None:
    """
    Call ingestor.ingest() with a consistent set of dummy metadata.
    Keeps individual tests focused on behaviour, not argument setup.
    """
    return ingestor.ingest(
        file_path=file_path,
        document_type=DocumentType.CREDIT,
        institution="TD",
        statement_period_start=date(2024, 1, 1),
        statement_period_end=date(2024, 1, 31),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path) -> str:
    """
    Return a path to a fresh SQLite DB in a pytest-managed temp directory.
    Uses pytest's built-in tmp_path fixture (avoids Windows file-lock issues
    with tempfile.TemporaryDirectory).
    """
    return str(tmp_path / "test.db")


@pytest.fixture()
def tmp_pdf(tmp_path) -> str:
    """
    Write a minimal valid PDF to a temp file and return its path.
    pdfplumber can open it; extract_text() will return "" for synthetic PDFs
    with no text layer, which is fine — we're testing plumbing, not OCR.
    """
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\n"
        b"startxref\n190\n%%EOF"
    )
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(pdf_bytes)
    return str(pdf_path)


# ---------------------------------------------------------------------------
# DocumentParser tests
# ---------------------------------------------------------------------------


class TestDocumentParser:
    """Tests for DocumentParser.extract_text() in isolation."""

    def test_returns_string(self, tmp_pdf):
        """extract_text() must always return a str, never None."""
        parser = DocumentParser()
        result = parser.extract_text(tmp_pdf)
        assert isinstance(result, str)

    def test_blank_page_does_not_raise(self, tmp_pdf):
        """
        Pages with no text layer return None from pdfplumber.
        extract_text() must handle this without raising TypeError.
        """
        parser = DocumentParser()
        # Should not raise even though the synthetic PDF has no text layer
        parser.extract_text(tmp_pdf)

    def test_multipage_joined_with_newline(self, tmp_path):
        """Pages should be joined with newlines, not concatenated directly."""
        parser = DocumentParser()
        fake_pages = [MagicMock(), MagicMock()]
        fake_pages[0].extract_text.return_value = "page one"
        fake_pages[1].extract_text.return_value = "page two"

        mock_pdf = MagicMock()
        mock_pdf.pages = fake_pages
        mock_pdf.__enter__ = lambda s: mock_pdf
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("fintrack.pipeline.ingestor.pdfplumber.open", return_value=mock_pdf):
            result = parser.extract_text("fake.pdf")

        assert result == "page one\npage two"

    def test_none_page_defaults_to_empty_string(self):
        """A page returning None from extract_text() should contribute '' not crash."""
        parser = DocumentParser()
        fake_page = MagicMock()
        fake_page.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.pages = [fake_page]
        mock_pdf.__enter__ = lambda s: mock_pdf
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("fintrack.pipeline.ingestor.pdfplumber.open", return_value=mock_pdf):
            result = parser.extract_text("fake.pdf")

        assert result == ""


# ---------------------------------------------------------------------------
# Ingestor._compute_hash tests
# ---------------------------------------------------------------------------


class TestComputeHash:
    """Tests for Ingestor._compute_hash() in isolation."""

    def test_returns_sha256_hex_digest(self, tmp_db, tmp_pdf):
        """Hash should match a manually computed SHA-256 of the same bytes."""
        ingestor = make_ingestor(tmp_db)
        expected = hashlib.sha256(Path(tmp_pdf).read_bytes()).hexdigest()
        assert ingestor._compute_hash(tmp_pdf) == expected

    def test_hash_is_64_chars(self, tmp_db, tmp_pdf):
        """SHA-256 hex digest is always 64 lowercase characters."""
        ingestor = make_ingestor(tmp_db)
        result = ingestor._compute_hash(tmp_pdf)
        assert len(result) == 64
        assert result == result.lower()

    def test_different_files_different_hashes(self, tmp_db, tmp_path):
        """Two files with different content must produce different hashes."""
        ingestor = make_ingestor(tmp_db)
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(b"content a")
        f2.write_bytes(b"content b")
        assert ingestor._compute_hash(str(f1)) != ingestor._compute_hash(str(f2))


# ---------------------------------------------------------------------------
# Ingestor.ingest tests
# ---------------------------------------------------------------------------


class TestIngest:
    """End-to-end tests for the full ingestion flow."""

    def test_returns_raw_document(self, tmp_db, tmp_pdf):
        """A fresh PDF should return a populated RawDocument."""
        ingestor = make_ingestor(tmp_db)
        result = ingest_dummy(ingestor, tmp_pdf)
        assert isinstance(result, RawDocument)

    def test_persists_institution(self, tmp_db, tmp_pdf):
        """Institution metadata should be stored on the returned document."""
        ingestor = make_ingestor(tmp_db)
        result = ingest_dummy(ingestor, tmp_pdf)
        assert result.institution == "TD"

    def test_parsed_flag_is_false(self, tmp_db, tmp_pdf):
        """parsed must remain False after ingestion — normalizer sets it in Step 2."""
        ingestor = make_ingestor(tmp_db)
        result = ingest_dummy(ingestor, tmp_pdf)
        assert result.parsed is False

    def test_duplicate_returns_none(self, tmp_db, tmp_pdf):
        """Re-ingesting the same file should return None (dedup via file_hash)."""
        ingestor = make_ingestor(tmp_db)
        ingest_dummy(ingestor, tmp_pdf)
        second = ingest_dummy(ingestor, tmp_pdf)
        assert second is None

    def test_duplicate_does_not_insert_twice(self, tmp_db, tmp_pdf):
        """Only one row should exist in raw_documents after two ingest calls."""
        from fintrack.core.db import fetch_all, raw_documents
        from sqlalchemy import create_engine

        ingestor = make_ingestor(tmp_db)
        ingest_dummy(ingestor, tmp_pdf)
        ingest_dummy(ingestor, tmp_pdf)

        with ingestor.engine.connect() as conn:
            rows = fetch_all(conn, raw_documents)
        assert len(rows) == 1

    def test_different_files_both_ingested(self, tmp_db, tmp_path):
        """Two distinct PDFs should each produce a RawDocument row."""
        from fintrack.core.db import fetch_all, raw_documents

        # Minimal but different PDF bytes so hashes differ
        f1 = tmp_path / "jan.pdf"
        f2 = tmp_path / "feb.pdf"
        f1.write_bytes(b"%PDF-1.4 file one")
        f2.write_bytes(b"%PDF-1.4 file two")

        ingestor = make_ingestor(tmp_db)
        ingest_dummy(ingestor, str(f1))
        ingest_dummy(ingestor, str(f2))

        with ingestor.engine.connect() as conn:
            rows = fetch_all(conn, raw_documents)
        assert len(rows) == 2
