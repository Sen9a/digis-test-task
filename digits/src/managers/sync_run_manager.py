from typing import Any, Sequence

from .base import BaseManager
from sqlalchemy import select, Row, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import sync_runs_table
from src.models import SyncRun

class SyncRunManager(BaseManager):

    async def create_run(self, run: SyncRun) -> None:
        """Create a new sync run."""
        async with self._session_factory() as session:
            stmt = pg_insert(sync_runs_table).values(
                id=run.id,
                tenant_id=run.tenant_id,
                source_connector=run.source_connector,
                target_connector=run.target_connector,
                status=run.status.value,
                cursor_position=run.cursor_position,
                records_processed=run.records_processed,
                records_succeeded=run.records_succeeded,
                records_failed=run.records_failed,
                records_skipped=run.records_skipped,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
            await session.execute(stmt)
            await session.commit()

    async def get_run(self, run_id: str) -> Row[tuple[Any]] | None:
        """Get a sync run by ID."""
        async with self._session_factory() as session:
            stmt = select(sync_runs_table).where(sync_runs_table.c.id == run_id)
            result = await session.execute(stmt)
            row = result.fetchone()
            if row is None:
                return None
            return row

    async def update_run(self, run: SyncRun) -> None:
        """Update a sync run."""
        async with self._session_factory() as session:
            stmt = pg_insert(sync_runs_table).values(
                id=run.id,
                tenant_id=run.tenant_id,
                source_connector=run.source_connector,
                target_connector=run.target_connector,
                status=run.status.value,
                cursor_position=run.cursor_position,
                records_processed=run.records_processed,
                records_succeeded=run.records_succeeded,
                records_failed=run.records_failed,
                records_skipped=run.records_skipped,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "status": stmt.excluded.status,
                    "cursor_position": stmt.excluded.cursor_position,
                    "records_processed": stmt.excluded.records_processed,
                    "records_succeeded": stmt.excluded.records_succeeded,
                    "records_failed": stmt.excluded.records_failed,
                    "records_skipped": stmt.excluded.records_skipped,
                    "completed_at": stmt.excluded.completed_at,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_runs_for_tenant(self, tenant_id: str) -> Sequence[Row[tuple[Any]]]:
        """Get all runs for a tenant."""
        async with self._session_factory() as session:
            stmt = select(sync_runs_table).where(sync_runs_table.c.tenant_id == tenant_id)
            result = await session.execute(stmt)
            rows = result.fetchall()
            return rows

    async def clear(self) -> None:
        """Clear all state (for testing)."""
        async with self._session_factory() as session:
            await session.execute(delete(sync_runs_table))
            await session.commit()