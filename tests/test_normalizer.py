"""
tests/test_normalizer.py

Unit tests for pipeline/normalizer.py.
Covers TransactionExtractor and Normalizer independently.

Run from project root: pytest tests/test_normalizer.py -v
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from fintrack.core.models import (
    DocumentType,
    NormalizedTransaction,
    RawDocument,
    TransactionType,
)
from fintrack.pipeline.normalizer import Normalizer, TransactionExtractor


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path) -> str:
    """Fresh SQLite DB path for each test."""
    return str(tmp_path / "test.db")


@pytest.fixture()
def raw_doc(tmp_db) -> RawDocument:
    """A persisted RawDocument to use as the normalizer's input."""
    from fintrack.core.db import get_engine, init_db, insert_row, raw_documents

    doc = RawDocument(
        file_path="/data/raw/td_jan2024.pdf",
        file_hash="abc123",
        document_type=DocumentType.CREDIT,
        institution="TD",
        statement_period_start=date(2024, 1, 1),
        statement_period_end=date(2024, 1, 31),
    )
    engine = get_engine(tmp_db)
    init_db(engine)
    with engine.connect() as conn:
        insert_row(conn, raw_documents, doc.model_dump())
    return doc


@pytest.fixture()
def sample_llm_transactions() -> list[dict]:
    """
    A realistic LLM response payload — three transactions covering
    debit, credit, and a null merchant to exercise all branches.
    """
    return [
        {
            "date": "2024-01-15",
            "description_raw": "AMZN*AB12CD VANCOUVER",
            "description_clean": "Amazon purchase",
            "merchant_name": "Amazon",
            "amount": 49.99,
            "transaction_type": "debit",
        },
        {
            "date": "2024-01-17",
            "description_raw": "TIM HORTONS #1234",
            "description_clean": "Tim Hortons",
            "merchant_name": "Tim Hortons",
            "amount": 4.75,
            "transaction_type": "debit",
        },
        {
            "date": "2024-01-20",
            "description_raw": "PAYROLL DEPOSIT",
            "description_clean": "Payroll deposit",
            "merchant_name": None,
            "amount": 3200.00,
            "transaction_type": "credit",
        },
    ]


# ---------------------------------------------------------------------------
# TransactionExtractor — build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    """Verify the prompt is correctly filled before touching the LLM."""

    def test_placeholder_is_replaced(self):
        extractor = TransactionExtractor()
        result = extractor.build_prompt("some raw text")
        assert "{raw_text}" not in result

    def test_raw_text_appears_in_prompt(self):
        extractor = TransactionExtractor()
        result = extractor.build_prompt("AMZN*AB12CD VANCOUVER -49.99")
        assert "AMZN*AB12CD VANCOUVER -49.99" in result

    def test_returns_string(self):
        extractor = TransactionExtractor()
        assert isinstance(extractor.build_prompt("text"), str)


# ---------------------------------------------------------------------------
# TransactionExtractor — parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    """parse_response must handle clean JSON, markdown fences, and bad input."""

    def test_parses_clean_json_array(self, sample_llm_transactions):
        extractor = TransactionExtractor()
        raw = json.dumps(sample_llm_transactions)
        result = extractor.parse_response(raw)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_raises_on_invalid_json(self):
        extractor = TransactionExtractor()
        with pytest.raises(ValueError, match="could not be parsed"):
            extractor.parse_response("this is not json")

    def test_raises_when_result_is_not_list(self):
        extractor = TransactionExtractor()
        with pytest.raises(ValueError, match="Expected a JSON array"):
            extractor.parse_response(json.dumps({"key": "value"}))

    def test_handles_whitespace(self, sample_llm_transactions):
        extractor = TransactionExtractor()
        raw = f"\n\n  {json.dumps(sample_llm_transactions)}  \n"
        result = extractor.parse_response(raw)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TransactionExtractor — extract (mocked LLM)
# ---------------------------------------------------------------------------


class TestExtract:
    """End-to-end extract() with the LLM call mocked out."""

    def test_returns_list_of_dicts(self, sample_llm_transactions):
        extractor = TransactionExtractor()
        extractor.llm = MagicMock()
        extractor.llm.invoke.return_value = json.dumps(sample_llm_transactions)

        result = extractor.extract("raw statement text")
        assert isinstance(result, list)
        assert len(result) == 3

    def test_llm_is_called_once(self, sample_llm_transactions):
        extractor = TransactionExtractor()
        extractor.llm = MagicMock()
        extractor.llm.invoke.return_value = json.dumps(sample_llm_transactions)

        extractor.extract("raw statement text")
        extractor.llm.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# Normalizer — _build_transaction
# ---------------------------------------------------------------------------


class TestBuildTransaction:
    """Verify _build_transaction converts raw dicts to valid Pydantic models."""

    @pytest.fixture()
    def normalizer(self, tmp_db):
        with patch("fintrack.pipeline.normalizer.TransactionExtractor"):
            return Normalizer(db_path=tmp_db)

    def test_returns_normalized_transaction(self, normalizer, sample_llm_transactions):
        txn = normalizer._build_transaction(sample_llm_transactions[0], "doc-id-123")
        assert isinstance(txn, NormalizedTransaction)

    def test_date_parsed_correctly(self, normalizer, sample_llm_transactions):
        txn = normalizer._build_transaction(sample_llm_transactions[0], "doc-id-123")
        assert txn.date == date(2024, 1, 15)

    def test_transaction_type_enum(self, normalizer, sample_llm_transactions):
        debit = normalizer._build_transaction(sample_llm_transactions[0], "doc-id-123")
        credit = normalizer._build_transaction(sample_llm_transactions[2], "doc-id-123")
        assert debit.transaction_type == TransactionType.DEBIT
        assert credit.transaction_type == TransactionType.CREDIT

    def test_amount_is_positive(self, normalizer, sample_llm_transactions):
        txn = normalizer._build_transaction(sample_llm_transactions[0], "doc-id-123")
        assert txn.amount > 0

    def test_null_merchant_allowed(self, normalizer, sample_llm_transactions):
        txn = normalizer._build_transaction(sample_llm_transactions[2], "doc-id-123")
        assert txn.merchant_name is None

    def test_category_fields_are_none(self, normalizer, sample_llm_transactions):
        """Category, subcategory, is_recurring must be None — categorizer sets these."""
        txn = normalizer._build_transaction(sample_llm_transactions[0], "doc-id-123")
        assert txn.category is None
        assert txn.subcategory is None
        assert txn.is_recurring is None

    def test_raw_document_id_set(self, normalizer, sample_llm_transactions):
        txn = normalizer._build_transaction(sample_llm_transactions[0], "doc-id-123")
        assert txn.raw_document_id == "doc-id-123"


# ---------------------------------------------------------------------------
# Normalizer — normalize (mocked LLM, real DB)
# ---------------------------------------------------------------------------


class TestNormalize:
    """Full normalize() flow with a mocked LLM and real SQLite DB."""

    @pytest.fixture()
    def normalizer(self, tmp_db, sample_llm_transactions):
        """Normalizer with the LLM mocked to return sample_llm_transactions."""
        n = Normalizer(db_path=tmp_db)
        n.extractor = MagicMock()
        n.extractor.extract.return_value = sample_llm_transactions
        return n

    def test_returns_list_of_normalized_transactions(self, normalizer, raw_doc):
        result = normalizer.normalize(raw_doc, "raw text")
        assert all(isinstance(t, NormalizedTransaction) for t in result)

    def test_correct_count_persisted(self, normalizer, raw_doc, tmp_db):
        from fintrack.core.db import fetch_all, get_engine, normalized_transactions

        normalizer.normalize(raw_doc, "raw text")
        with get_engine(tmp_db).connect() as conn:
            rows = fetch_all(conn, normalized_transactions)
        assert len(rows) == 3

    def test_parsed_flag_flipped(self, normalizer, raw_doc, tmp_db):
        from fintrack.core.db import fetch_all, get_engine, raw_documents

        normalizer.normalize(raw_doc, "raw text")
        with get_engine(tmp_db).connect() as conn:
            rows = fetch_all(conn, raw_documents, {"id": raw_doc.id})
        assert rows[0]["parsed"] is True

    def test_provenance_fk_set_correctly(self, normalizer, raw_doc, tmp_db):
        from fintrack.core.db import fetch_all, get_engine, normalized_transactions

        normalizer.normalize(raw_doc, "raw text")
        with get_engine(tmp_db).connect() as conn:
            rows = fetch_all(conn, normalized_transactions)
        assert all(r["raw_document_id"] == raw_doc.id for r in rows)

    def test_amounts_stored_correctly(self, normalizer, raw_doc, tmp_db):
        from fintrack.core.db import fetch_all, get_engine, normalized_transactions

        normalizer.normalize(raw_doc, "raw text")
        with get_engine(tmp_db).connect() as conn:
            rows = fetch_all(conn, normalized_transactions)
        amounts = sorted(r["amount"] for r in rows)
        assert amounts == [4.75, 49.99, 3200.00]
