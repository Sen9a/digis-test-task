"""PostgreSQL-backed state store using SQLAlchemy ORM."""
from __future__ import annotations

from typing import Any, Sequence

from .base import BaseManager

from sqlalchemy import select, Row, func, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.tables import SyncStateStatusTable
from src.models import SyncState, SyncStateStatus

class SyncStatesManager(BaseManager):

    async def get_state(
        self,
        tenant_id: str,
        source_connector: str,
        source_record_id: str,
    ) -> SyncStateStatusTable | None:
        """Get sync state for a specific record."""
        async with self.session_factory() as session:
            stmt = select(SyncStateStatusTable).where(
                SyncStateStatusTable.tenant_id == tenant_id,
                SyncStateStatusTable.source_connector == source_connector,
                SyncStateStatusTable.source_record_id == source_record_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def save_state(self, state: SyncState) -> None:
        """Save or update sync state (upsert)."""
        async with self.session_factory() as session:
            stmt = pg_insert(SyncStateStatusTable).values(
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
            await session.flush()

    async def get_states_by_status(
        self,
        tenant_id: str,
        status: SyncStateStatus,
        source_connector: str | None = None,
        target_connector: str | None = None,
    ) -> list[SyncStateStatusTable]:
        """Get all states with a given status for a tenant."""
        async with self.session_factory() as session:
            stmt = select(SyncStateStatusTable).where(
                SyncStateStatusTable.tenant_id == tenant_id,
                SyncStateStatusTable.status == status.value,
            )
            if source_connector is not None:
                stmt = stmt.where(
                    SyncStateStatusTable.source_connector == source_connector
                )
            if target_connector is not None:
                stmt = stmt.where(
                    SyncStateStatusTable.target_connector == target_connector
                )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_all_states(self, tenant_id: str) -> list[SyncStateStatusTable]:
        """Get all states for a tenant."""
        async with self.session_factory() as session:
            stmt = select(SyncStateStatusTable).where(
                SyncStateStatusTable.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def count_states(self, tenant_id: str) -> Sequence[Row[tuple[Any, Any]]]:
        """Count states by status for a tenant."""
        async with self.session_factory() as session:
            stmt = (
                select(SyncStateStatusTable.status, func.count())
                .where(SyncStateStatusTable.tenant_id == tenant_id)
                .group_by(SyncStateStatusTable.status)
            )
            result = await session.execute(stmt)
            return result.fetchall()

    async def clear(self) -> None:
        """Clear all state (for testing)."""
        async with self.session_factory() as session:
            await session.execute(delete(SyncStateStatusTable))
            await session.flush()
