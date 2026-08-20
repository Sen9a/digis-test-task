from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SyncStateStatus(str, Enum):
    """Status of a single record in the sync pipeline."""

    PENDING = "pending"
    EXPORTED = "exported"
    FAILED = "failed"
    SKIPPED_UNCHANGED = "skipped_unchanged"


class SyncState(BaseModel):
    """
    Tracks the sync state of a single invoice from source to target.

    This is the source of truth for "what happened to invoice X from tenant Y?"
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    source_connector: str
    source_record_id: str
    target_connector: str
    target_record_id: str | None = None
    content_hash: str
    status: SyncStateStatus = SyncStateStatus.PENDING
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def mark_attempt(self, error: str | None = None) -> None:
        """Record a sync attempt."""
        self.attempt_count += 1
        self.last_attempt_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)
        if error:
            self.last_error = error

    def mark_exported(self, target_record_id: str) -> None:
        """Mark as successfully exported."""
        self.status = SyncStateStatus.EXPORTED
        self.target_record_id = target_record_id
        self.mark_attempt()

    def mark_failed(self, error: str) -> None:
        """Mark as failed."""
        self.status = SyncStateStatus.FAILED
        self.mark_attempt(error)

    def mark_skipped(self) -> None:
        """Mark as skipped (unchanged)."""
        self.status = SyncStateStatus.SKIPPED_UNCHANGED
        self.updated_at = datetime.now(UTC)
