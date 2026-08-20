from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    Table
)
from sqlalchemy.sql import func
from sqlalchemy import String
from .sql_metadata import metadata

sync_runs_table = Table(
    "sync_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(255), nullable=False),
    Column("source_connector", String(50), nullable=False),
    Column("target_connector", String(50), nullable=False),
    Column("status", String(20), nullable=False),
    Column("cursor_position", String(255), nullable=True),
    Column("records_processed", Integer, default=0),
    Column("records_succeeded", Integer, default=0),
    Column("records_failed", Integer, default=0),
    Column("records_skipped", Integer, default=0),
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)