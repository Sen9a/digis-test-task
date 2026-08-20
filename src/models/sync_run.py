from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class SyncRunStatus(str, Enum):
    """Status of a sync run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class SyncRun(BaseModel):
    """
    Tracks a single sync execution.

    A sync run processes a batch of invoices from source to target.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    source_connector: str
    target_connector: str
    status: SyncRunStatus = SyncRunStatus.RUNNING
    cursor_position: str | None = None  # For incremental sync resumption
    records_processed: int = 0
    records_succeeded: int = 0
    records_failed: int = 0
    records_skipped: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def complete(self) -> None:
        """Mark run as completed."""
        self.status = SyncRunStatus.COMPLETED
        self.completed_at = datetime.now(UTC)

    def fail(self) -> None:
        """Mark run as failed."""
        self.status = SyncRunStatus.FAILED
        self.completed_at = datetime.now(UTC)

    @property
    def duration_seconds(self) -> float | None:
        """Duration in seconds, None if still running."""
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()
