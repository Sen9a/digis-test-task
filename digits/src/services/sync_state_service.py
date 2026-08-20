import logging

from dataclasses import dataclass

from src.managers.sync_states_manager import SyncStatesManager
from src.models import SyncState, SyncStateStatus


@dataclass
class SyncStateService:
    manager: SyncStatesManager
    logger: logging.Logger

    @staticmethod
    async def sync_state(*,
                         tenant_id,
                         source_name,
                         source_record_id,
                         target_name,
                         content_hash) -> SyncState:
        return SyncState(
                tenant_id=tenant_id,
                source_connector=source_name,
                source_record_id=source_record_id,
                target_connector=target_name,
                content_hash=content_hash or "",
        )

    @staticmethod
    def _row_to_sync_state(row) -> SyncState:
        """Convert a database row to a SyncState Pydantic model."""
        return SyncState(
            id=row.id,
            tenant_id=row.tenant_id,
            source_connector=row.source_connector,
            source_record_id=row.source_record_id,
            target_connector=row.target_connector,
            target_record_id=row.target_record_id,
            content_hash=row.content_hash,
            status=SyncStateStatus(row.status),
            attempt_count=row.attempt_count,
            last_attempt_at=row.last_attempt_at,
            last_error=row.last_error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_state(self,
                        tenant_id: str,
                        source_connector: str,
                        source_record_id: str) -> SyncState | None:
        row = await self.manager.get_state(tenant_id, source_connector, source_record_id)
        if row:
            return self._row_to_sync_state(row)
        return None

    async def save_state(self, state: SyncState) -> None:
        await self.manager.save_state(state)

    async def get_states_by_status(self, tenant_id: str, status: SyncStateStatus) -> list[SyncState]:
        rows = await self.manager.get_states_by_status(tenant_id, status)
        return [self._row_to_sync_state(row) for row in rows]

    async def get_all_states(self, tenant_id: str) -> list[SyncState]:
        rows = await self.manager.get_all_states(tenant_id)
        return [self._row_to_sync_state(row) for row in rows]

    async def count_states(self, tenant_id: str) -> dict[str, str]:
        rows = await self.manager.count_states(tenant_id)
        return {row[0]: row[1] for row in rows}