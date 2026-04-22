"""
core/db.py

SQLAlchemy Core — table definitions, engine factory, and thin CRUD helpers.

Why SQLAlchemy Core (not ORM)?
  - Full control over SQL without magic
  - Easy to swap SQLite → Postgres later (change the connection string, nothing else)
  - Alembic migrations work cleanly on top of Core
  - Pydantic models stay as the canonical shape; db tables mirror them explicitly

Schema mirrors models.py 1-to-1.
If you change a model, add an Alembic migration — don't edit this file's columns directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
)
from sqlalchemy.engine import Connection, Engine

from .config import DB_PATH

# ---------------------------------------------------------------------------
# Metadata registry — all tables declared here
# ---------------------------------------------------------------------------

metadata = MetaData()

# ---------------------------------------------------------------------------
# Table: raw_documents
# ---------------------------------------------------------------------------

raw_documents = Table(
    "raw_documents",
    metadata,
    Column("id", String, primary_key=True),
    Column("file_path", String, nullable=False),
    Column("file_hash", String, nullable=False, unique=True),  # dedup key
    Column("document_type", String, nullable=False),
    Column("institution", String, nullable=False),
    Column("statement_period_start", Date, nullable=False),
    Column("statement_period_end", Date, nullable=False),
    Column("ingested_at", DateTime, nullable=False),
    Column("parsed", Boolean, default=False, nullable=False),
)

# ---------------------------------------------------------------------------
# Table: normalized_transactions
# ---------------------------------------------------------------------------

normalized_transactions = Table(
    "normalized_transactions",
    metadata,
    Column("id", String, primary_key=True),
    Column("raw_document_id", String, nullable=False),  # provenance FK
    Column("date", Date, nullable=False),
    Column("description_raw", Text, nullable=False),
    Column("description_clean", Text, nullable=False),
    Column("merchant_name", String),
    Column("amount", Float, nullable=False),
    Column("transaction_type", String, nullable=False),
    Column("category", String),
    Column("subcategory", String),
    Column("is_recurring", Boolean),
    Column("notes", Text),
    Column("created_at", DateTime, nullable=False),
)

# ---------------------------------------------------------------------------
# Table: merchant_records
# ---------------------------------------------------------------------------

merchant_records = Table(
    "merchant_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("raw_name", String, nullable=False, unique=True),  # lookup key
    Column("canonical_name", String, nullable=False),
    Column("category", String),
    Column("created_at", DateTime, nullable=False),
)

# ---------------------------------------------------------------------------
# Table: user_input_events
# ---------------------------------------------------------------------------

user_input_events = Table(
    "user_input_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("description", String, nullable=False),
    Column("expected_date", Date, nullable=False),
    Column("estimated_amount", Float, nullable=False),
    Column("category", String),
    Column("is_recurring", Boolean, default=False, nullable=False),
    Column("recurrence_months", Integer),
    Column("created_at", DateTime, nullable=False),
)

# ---------------------------------------------------------------------------
# Table: execution_logs
# ---------------------------------------------------------------------------

execution_logs = Table(
    "execution_logs",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),  # groups all steps in one run
    Column("step_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("input_summary", Text),
    Column("output_summary", Text),
    Column("error", Text),
    Column("duration_ms", Integer),
    Column("created_at", DateTime, nullable=False),
)

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def get_engine(db_path: str = DB_PATH) -> Engine:
    """
    Create and return a SQLAlchemy engine.
    Creates the data/ directory if it doesn't exist yet.
    Swap sqlite:/// for postgresql:// later with zero other changes.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{db_path}",
        echo=False,  # set True to log all SQL during development
        future=True,  # use SQLAlchemy 2.0 style
    )


def init_db(engine: Engine) -> None:
    """
    Create all tables if they don't already exist.
    Safe to call on every startup — won't overwrite existing data.
    For schema changes, use Alembic migrations instead.
    """
    metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Thin CRUD helpers
# ---------------------------------------------------------------------------
# These are intentionally minimal — business logic lives in pipeline/ modules.
# Each helper takes a Connection (from `with engine.connect() as conn:`)
# so callers control transaction boundaries explicitly.


def insert_row(conn: Connection, table: Table, row: dict[str, Any]) -> None:
    """Insert a single row dict into a table."""
    conn.execute(insert(table).values(**row))
    conn.commit()


def insert_rows(conn: Connection, table: Table, rows: list[dict[str, Any]]) -> None:
    """Bulk insert a list of row dicts. Single transaction."""
    if not rows:
        return
    conn.execute(insert(table), rows)
    conn.commit()


def row_exists(conn: Connection, table: Table, column: str, value: Any) -> bool:
    """
    Check if a row exists by a single column value.
    Primary use: dedup checks (file_hash, raw_name, transaction id).
    """
    col = table.c[column]
    result = conn.execute(select(table).where(col == value).limit(1))
    return result.fetchone() is not None


def fetch_all(
    conn: Connection,
    table: Table,
    filters: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """
    Fetch all rows from a table, optionally filtered by column equality.
    Returns list of dicts — ready to unpack into Pydantic models.

    Example:
        rows = fetch_all(conn, normalized_transactions, {"category": "Groceries"})
        txns = [NormalizedTransaction(**r) for r in rows]
    """
    stmt = select(table)
    if filters:
        for col_name, val in filters.items():
            stmt = stmt.where(table.c[col_name] == val)
    result = conn.execute(stmt)
    return [dict(row._mapping) for row in result]
