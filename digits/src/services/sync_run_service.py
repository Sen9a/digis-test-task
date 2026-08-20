import logging

from dataclasses import dataclass

from src.managers.sync_run_manager import SyncRunManager
from src.models import SyncRun, SyncRunStatus


@dataclass
class SyncRunService:
    manager: SyncRunManager
    logger: logging.Logger

    @staticmethod
    def _row_to_run_state(row) -> SyncRun:
        """Convert a database row to a SyncRun Pydantic model."""
        return SyncRun(
                id=row.id,
                tenant_id=row.tenant_id,
                source_connector=row.source_connector,
                target_connector=row.target_connector,
                status=SyncRunStatus(row.status),
                cursor_position=row.cursor_position,
                records_processed=row.records_processed,
                records_succeeded=row.records_succeeded,
                records_failed=row.records_failed,
                records_skipped=row.records_skipped,
                started_at=row.started_at,
                completed_at=row.completed_at,
        )

    async def create_run(self,
                         *,
                         tenant_id: str,
                         source_connector: str,
                         target_connector: str) -> SyncRun:
        run = SyncRun(
            tenant_id=tenant_id,
            source_connector=source_connector,
            target_connector=target_connector,
        )
        await self.manager.create_run(run)

        self.logger.info(
            "Starting sync run %s for tenant %s: %s → %s",
            run.id,
            run.tenant_id,
            source_connector,
            target_connector,
        )
        return run

    async def update_run(self, run: SyncRun) -> None:
        await self.manager.update_run(run)

    async def get_run(self, run_id: str) -> SyncRun | None:
        row = await self.manager.get_run(run_id)
        if row:
            return self._row_to_run_state(row)
        return None
