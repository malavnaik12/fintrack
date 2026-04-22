"""
tests/test_db.py

Pytest suite for core/models.py + core/db.py.
Run from project root: pytest tests/test_db.py -v
"""

import os
import tempfile
from datetime import date

import pytest

from src.fintrack.core.models import (
    DocumentType,
    ExecutionLog,
    MerchantRecord,
    NormalizedTransaction,
    RawDocument,
    RunStatus,
    TransactionType,
    UserInputEvent,
)
from src.fintrack.core.db import (
    execution_logs,
    fetch_all,
    get_engine,
    init_db,
    insert_row,
    merchant_records,
    normalized_transactions,
    raw_documents,
    row_exists,
    user_input_events,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_conn():
    """
    Spins up a clean in-memory SQLite DB for the whole test module.
    All tests share one connection so foreign-key relationships work
    (e.g. NormalizedTransaction references RawDocument.id).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = get_engine(os.path.join(tmpdir, "test.db"))
        init_db(engine)
        with engine.connect() as conn:
            yield conn
        engine.dispose()


@pytest.fixture(scope="module")
def raw_doc(db_conn):
    """Insert one RawDocument and return it — shared across tests that need it."""
    doc = RawDocument(
        file_path="/data/raw/td_jan2024.pdf",
        file_hash="abc123",
        document_type=DocumentType.CREDIT,
        institution="TD",
        statement_period_start=date(2024, 1, 1),
        statement_period_end=date(2024, 1, 31),
    )
    insert_row(db_conn, raw_documents, doc.model_dump())
    return doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRawDocument:
    def test_insert_and_dedup_check(self, db_conn, raw_doc):
        """Inserted document should be findable by its SHA-256 hash."""
        assert row_exists(db_conn, raw_documents, "file_hash", "abc123")

    def test_dedup_key_is_file_hash(self, db_conn, raw_doc):
        """A different hash should not match."""
        assert not row_exists(db_conn, raw_documents, "file_hash", "not_a_real_hash")


class TestNormalizedTransaction:
    @pytest.fixture(scope="class")
    def txn(self, db_conn, raw_doc):
        t = NormalizedTransaction(
            raw_document_id=raw_doc.id,
            date=date(2024, 1, 15),
            description_raw="AMZN*AB12CD VANCOUVER",
            description_clean="Amazon purchase",
            merchant_name="Amazon",
            amount=49.99,
            transaction_type=TransactionType.DEBIT,
            category="Shopping",
            subcategory="Online Retail",
            is_recurring=False,
        )
        insert_row(db_conn, normalized_transactions, t.model_dump())
        return t

    def test_fetch_by_category(self, db_conn, txn):
        rows = fetch_all(db_conn, normalized_transactions, {"category": "Shopping"})
        assert len(rows) == 1

    def test_pydantic_round_trip(self, db_conn, txn):
        rows = fetch_all(db_conn, normalized_transactions, {"category": "Shopping"})
        recovered = NormalizedTransaction(**rows[0])
        assert recovered.merchant_name == "Amazon"
        assert recovered.amount == 49.99

    def test_amount_is_positive(self, db_conn, txn):
        rows = fetch_all(db_conn, normalized_transactions, {"category": "Shopping"})
        recovered = NormalizedTransaction(**rows[0])
        assert recovered.amount > 0

    def test_transaction_type_is_debit(self, db_conn, txn):
        rows = fetch_all(db_conn, normalized_transactions, {"category": "Shopping"})
        recovered = NormalizedTransaction(**rows[0])
        assert recovered.transaction_type == TransactionType.DEBIT


class TestMerchantRecord:
    @pytest.fixture(scope="class")
    def merchant(self, db_conn):
        m = MerchantRecord(
            raw_name="AMZN*AB12CD VANCOUVER",
            canonical_name="Amazon",
            category="Shopping",
        )
        insert_row(db_conn, merchant_records, m.model_dump())
        return m

    def test_insert_and_lookup(self, db_conn, merchant):
        assert row_exists(
            db_conn, merchant_records, "raw_name", "AMZN*AB12CD VANCOUVER"
        )

    def test_unknown_raw_name_not_found(self, db_conn, merchant):
        assert not row_exists(
            db_conn, merchant_records, "raw_name", "TOTALLY UNKNOWN MERCHANT"
        )


class TestUserInputEvent:
    @pytest.fixture(scope="class")
    def event(self, db_conn):
        e = UserInputEvent(
            description="Home insurance renewal",
            expected_date=date(2024, 3, 1),
            estimated_amount=2400.0,
            category="Insurance",
            is_recurring=True,
            recurrence_months=12,
        )
        insert_row(db_conn, user_input_events, e.model_dump())
        return e

    def test_insert_and_fetch(self, db_conn, event):
        rows = fetch_all(db_conn, user_input_events)
        assert len(rows) == 1

    def test_recurring_fields_persisted(self, db_conn, event):
        rows = fetch_all(db_conn, user_input_events)
        recovered = UserInputEvent(**rows[0])
        assert recovered.is_recurring is True
        assert recovered.recurrence_months == 12


class TestExecutionLog:
    @pytest.fixture(scope="class")
    def log(self, db_conn):
        entry = ExecutionLog(
            run_id="run-001",
            step_name="smoke_test",
            status=RunStatus.SUCCESS,
            input_summary="1 PDF",
            output_summary="1 transaction",
            duration_ms=42,
        )
        insert_row(db_conn, execution_logs, entry.model_dump())
        return entry

    def test_insert_and_fetch_by_run_id(self, db_conn, log):
        rows = fetch_all(db_conn, execution_logs, {"run_id": "run-001"})
        assert len(rows) == 1

    def test_step_name_persisted(self, db_conn, log):
        rows = fetch_all(db_conn, execution_logs, {"run_id": "run-001"})
        assert rows[0]["step_name"] == "smoke_test"

    def test_status_is_success(self, db_conn, log):
        rows = fetch_all(db_conn, execution_logs, {"run_id": "run-001"})
        recovered = ExecutionLog(**rows[0])
        assert recovered.status == RunStatus.SUCCESS
