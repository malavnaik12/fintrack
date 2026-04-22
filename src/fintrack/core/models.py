"""
core/models.py

Pydantic v2 models for fin_intel.
These are the canonical data shapes — every layer of the system reads/writes these.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DocumentType(str, Enum):
    """The kind of financial account the statement came from."""

    CREDIT = "credit"
    DEBIT = "debit"


class TransactionType(str, Enum):
    """
    Direction of money flow from the account holder's perspective.
    DEBIT  = money left your account (expense, payment)
    CREDIT = money entered your account (refund, deposit, cashback)
    """

    DEBIT = "debit"
    CREDIT = "credit"


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# RawDocument
# Step 1 — provenance record for every PDF dropped into the system
# ---------------------------------------------------------------------------


class RawDocument(BaseModel):
    """
    Represents one PDF statement file.
    The file_hash (SHA-256) is the dedup key — re-ingesting the same file is a no-op.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str  # absolute path on disk
    file_hash: str  # SHA-256 of the file bytes
    document_type: DocumentType
    institution: str  # e.g. "TD", "RBC", "Amex"
    statement_period_start: date
    statement_period_end: date
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parsed: bool = False  # flipped to True after DocumentParser runs


# ---------------------------------------------------------------------------
# NormalizedTransaction
# Step 3 — the core object everything else is built on
# ---------------------------------------------------------------------------


class NormalizedTransaction(BaseModel):
    """
    A single, cleaned transaction extracted from a RawDocument.
    This is the source of truth — once populated, raw PDFs are no longer needed
    for analysis.

    Amount convention:
      - Always positive (absolute value of the transaction)
      - transaction_type tells you the direction
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    raw_document_id: str  # FK → RawDocument.id (provenance)

    date: date
    description_raw: str  # verbatim text from the PDF
    description_clean: str  # LLM-normalized, human-readable
    merchant_name: Optional[str] = None  # resolved by MerchantResolver
    amount: float  # always positive
    transaction_type: TransactionType

    category: Optional[str] = None  # e.g. "Groceries", "Travel"
    subcategory: Optional[str] = None  # e.g. "Supermarket", "Flight"
    is_recurring: Optional[bool] = None  # detected by CategoryEngine

    notes: Optional[str] = None  # free-form, user or LLM generated
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# MerchantRecord
# Step 5 — canonical merchant name registry
# ---------------------------------------------------------------------------


class MerchantRecord(BaseModel):
    """
    Maps raw merchant strings (messy, bank-formatted) to a canonical name.
    e.g. "AMZN*AB12CD Vancouver" → "Amazon"
    Built up incrementally; new raw_names are resolved by LLM then cached here.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    raw_name: str  # unique — the exact string seen in statements
    canonical_name: str  # clean, display-ready name
    category: Optional[str] = None  # category hint for CategoryEngine
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# UserInputEvent
# Step 8 — user-declared future expenses for forecasting
# ---------------------------------------------------------------------------


class UserInputEvent(BaseModel):
    """
    A known future expense the user manually declares.
    e.g. "Annual home insurance renewal — $2,400 — due March 1"
    Used by ForecastEngine to adjust projections.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    expected_date: date
    estimated_amount: float  # always positive
    category: Optional[str] = None

    is_recurring: bool = False
    recurrence_months: Optional[int] = None  # 1=monthly, 3=quarterly, 12=annual

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# ExecutionLog
# Step 11 — full traceability of every pipeline run
# ---------------------------------------------------------------------------


class ExecutionLog(BaseModel):
    """
    One log entry per pipeline step per run.
    run_id groups all steps in a single execution together.
    Mirrors the AgentTrace audit pattern.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str  # UUID shared across all steps in one run
    step_name: str  # e.g. "ingestor", "normalizer", "categorizer"
    status: RunStatus

    input_summary: Optional[str] = None  # brief description of what went in
    output_summary: Optional[str] = None  # brief description of what came out
    error: Optional[str] = None  # exception message/traceback on failure
    duration_ms: Optional[int] = None  # wall-clock time for the step

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
