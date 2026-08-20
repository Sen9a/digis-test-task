from __future__ import annotations

from typing import Any, Sequence
from .base import BaseManager
"""PostgreSQL-backed state store using SQLAlchemy Core."""

from sqlalchemy import select, Row, func, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import sync_states_table
from src.models import SyncState, SyncStateStatus

class SyncStatesManager(BaseManager):

    async def get_state(
        self,
        tenant_id: str,
        source_connector: str,
        source_record_id: str,
    ) -> Row[tuple[Any]] | None:
        """Get sync state for a specific record."""
        async with self._session_factory() as session:
            stmt = select(sync_states_table).where(
                sync_states_table.c.tenant_id == tenant_id,
                sync_states_table.c.source_connector == source_connector,
                sync_states_table.c.source_record_id == source_record_id,
            )
            result = await session.execute(stmt)
            row = result.fetchone()
            if row is None:
                return None
            return row

    async def save_state(self, state: SyncState) -> None:
        """Save or update sync state (upsert)."""
        async with self._session_factory() as session:
            stmt = pg_insert(sync_states_table).values(
                id=state.id,
                tenant_id=state.tenant_id,
                source_connector=state.source_connector,
                source_record_id=state.source_record_id,
                target_connector=state.target_connector,
                target_record_id=state.target_record_id,
                content_hash=state.content_hash,
                status=state.status.value,
                attempt_count=state.attempt_count,
                last_attempt_at=state.last_attempt_at,
                last_error=state.last_error,
                created_at=state.created_at,
                updated_at=state.updated_at,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_sync_state_tenant_source_record",
                set_={
                    "target_connector": stmt.excluded.target_connector,
                    "target_record_id": stmt.excluded.target_record_id,
                    "content_hash": stmt.excluded.content_hash,
                    "status": stmt.excluded.status,
                    "attempt_count": stmt.excluded.attempt_count,
                    "last_attempt_at": stmt.excluded.last_attempt_at,
                    "last_error": stmt.excluded.last_error,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_states_by_status(
        self,
        tenant_id: str,
        status: SyncStateStatus,
        source_connector: str | None = None,
        target_connector: str | None = None,
    ) -> Sequence[Row[tuple[Any]]]:
        """Get all states with a given status for a tenant."""
        async with self._session_factory() as session:
            stmt = select(sync_states_table).where(
                sync_states_table.c.tenant_id == tenant_id,
                sync_states_table.c.status == status.value,
            )
            if source_connector is not None:
                stmt = stmt.where(
                    sync_states_table.c.source_connector == source_connector
                )
            if target_connector is not None:
                stmt = stmt.where(
                    sync_states_table.c.target_connector == target_connector
                )
            result = await session.execute(stmt)
            rows = result.fetchall()
            return rows

    async def get_all_states(self, tenant_id: str) -> Sequence[Row[tuple[Any]]]:
        """Get all states for a tenant."""
        async with self._session_factory() as session:
            stmt = select(sync_states_table).where(
                sync_states_table.c.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            rows = result.fetchall()
            return rows

    async def count_states(self, tenant_id: str) -> Sequence[Row[tuple[Any, Any]]]:
        """Count states by status for a tenant."""
        async with self._session_factory() as session:
            stmt = (
                select(sync_states_table.c.status, func.count())
                .where(sync_states_table.c.tenant_id == tenant_id)
                .group_by(sync_states_table.c.status)
            )
            result = await session.execute(stmt)
            return result.fetchall()

    async def clear(self) -> None:
        """Clear all state (for testing)."""
        async with self._session_factory() as session:
            await session.execute(delete(sync_states_table))
            await session.commit()
