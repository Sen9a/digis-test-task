from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy import String
from .sql_metadata import metadata

sync_states_table = Table(
    "sync_states",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(255), nullable=False),
    Column("source_connector", String(50), nullable=False),
    Column("source_record_id", String(255), nullable=False),
    Column("target_connector", String(50), nullable=False),
    Column("target_record_id", String(255), nullable=True),
    Column("content_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempt_count", Integer, default=0),
    Column("last_attempt_at", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "tenant_id",
        "source_connector",
        "source_record_id",
        name="uq_sync_state_tenant_source_record",
    ),
)