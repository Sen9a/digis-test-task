from __future__ import annotations

from dataclasses import dataclass, field

from src.models import SyncRun, SyncState, SyncStateStatus


@dataclass
class StateStore:
    """
    In-memory state store for sync state and run tracking.

    In production, this would be PostgreSQL. For the test task,
    in-memory is sufficient and keeps the focus on sync logic.
    """

    states: dict[tuple[str, str, str], SyncState] = field(default_factory=dict)
    runs: dict[str, SyncRun] = field(default_factory=dict)

    # --- Sync State Operations ---

    def get_state(
        self,
        tenant_id: str,
        source_connector: str,
        source_record_id: str,
    ) -> SyncState | None:
        """Get sync state for a specific record."""
        key = (tenant_id, source_connector, source_record_id)
        return self.states.get(key)

    def save_state(self, state: SyncState) -> None:
        """Save or update sync state."""
        key = (state.tenant_id, state.source_connector, state.source_record_id)
        self.states[key] = state

    def get_states_by_status(
        self,
        tenant_id: str,
        status: SyncStateStatus,
    ) -> list[SyncState]:
        """Get all states with a given status for a tenant."""
        return [
            s
            for s in self.states.values()
            if s.tenant_id == tenant_id and s.status == status
        ]

    def get_all_states(self, tenant_id: str) -> list[SyncState]:
        """Get all states for a tenant."""
        return [s for s in self.states.values() if s.tenant_id == tenant_id]

    # --- Sync Run Operations ---

    def create_run(self, run: SyncRun) -> None:
        """Create a new sync run."""
        self.runs[run.id] = run

    def get_run(self, run_id: str) -> SyncRun | None:
        """Get a sync run by ID."""
        return self.runs.get(run_id)

    def update_run(self, run: SyncRun) -> None:
        """Update a sync run."""
        self.runs[run.id] = run

    def get_runs_for_tenant(self, tenant_id: str) -> list[SyncRun]:
        """Get all runs for a tenant."""
        return [r for r in self.runs.values() if r.tenant_id == tenant_id]

    # --- Stats ---

    def count_states(self, tenant_id: str) -> dict[str, int]:
        """Count states by status for a tenant."""
        counts: dict[str, int] = {}
        for state in self.states.values():
            if state.tenant_id == tenant_id:
                key = state.status.value
                counts[key] = counts.get(key, 0) + 1
        return counts

    def clear(self) -> None:
        """Clear all state (for testing)."""
        self.states.clear()
        self.runs.clear()
