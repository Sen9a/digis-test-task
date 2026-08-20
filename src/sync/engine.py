from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from src.abstract import SourceConnector, TargetConnector
from src.abstract.exceptions import (
    AuthenticationError,
    RateLimitError,
    SourceUnavailableError,
)
from src.const import ErrorCategory
from src.models import (
    Cursor,
    ExportResult,
    SyncError,
    SyncRun,
    SyncState,
    SyncStateStatus,
    UnifiedInvoice,
)
from src.sync.state import StateStore

logger = logging.getLogger(__name__)


@dataclass
class SyncEngine:
    """
    Core sync engine that orchestrates invoice synchronization.

    Handles:
    - Fetching from source with pagination
    - Normalization to unified model
    - Change detection via content hash
    - Idempotent export to target
    - Partial failure handling
    - State tracking for traceability
    """
    source: SourceConnector
    target: TargetConnector
    store: StateStore
    tenant_id: str
    max_retries: int = 3
    retry_base_delay: float = 1.0

    async def run(
        self,
        source_credentials: dict[str, Any],
        target_credentials: dict[str, Any],
        cursor: Cursor | None = None,
        batch_size: int = 100,
    ) -> SyncRun:
        """
        Execute a full sync run.

        Args:
            source_credentials: Credentials for source API
            target_credentials: Credentials for target API
            cursor: Resume from this cursor (for incremental sync)
            batch_size: Number of invoices per fetch batch

        Returns:
            SyncRun with results
        """
        run = SyncRun(
            tenant_id=self.tenant_id,
            source_connector=self.source.name,
            target_connector=self.target.name,
        )
        self.store.create_run(run)

        logger.info(
            "Starting sync run %s for tenant %s: %s → %s",
            run.id,
            self.tenant_id,
            self.source.name,
            self.target.name,
        )

        try:
            await self._authenticate(source_credentials, target_credentials)

            async for raw in self.source.fetch_all_invoices(
                cursor=cursor,
                batch_size=batch_size,
            ):
                run.records_processed += 1
                await self._process_invoice(raw, run)

            run.complete()
            logger.info(
                "Sync run %s completed: %d processed, %d succeeded, %d failed, %d skipped",
                run.id,
                run.records_processed,
                run.records_succeeded,
                run.records_failed,
                run.records_skipped,
            )

        except (AuthenticationError, SourceUnavailableError) as e:
            logger.error("Sync run %s failed: %s", run.id, e)
            run.fail()
        except Exception:
            logger.exception("Sync run %s failed unexpectedly", run.id)
            run.fail()

        self.store.update_run(run)
        return run

    async def _authenticate(
        self,
        source_credentials: dict[str, Any],
        target_credentials: dict[str, Any],
    ) -> None:
        """Authenticate both connectors."""
        await self.source.authenticate(source_credentials)
        await self.target.authenticate(target_credentials)

    async def _process_invoice(self, raw: Any, run: SyncRun) -> None:
        """
        Process a single invoice through the sync pipeline.

        Steps:
        1. Normalize raw → unified
        2. Compute content hash
        3. Check state store for previous sync
        4. Skip if unchanged
        5. Export to target with retry
        6. Update state store
        """
        source_record_id = raw.source_id

        try:
            # Normalize
            unified = self.source.normalize(raw)
            unified = unified.with_content_hash()

            # Check existing state
            existing = self.store.get_state(
                self.tenant_id,
                self.source.name,
                source_record_id,
            )

            # Skip if unchanged and previously exported
            if (
                existing
                and existing.content_hash == unified.content_hash
                and existing.status == SyncStateStatus.EXPORTED
            ):
                logger.debug("Skipping unchanged invoice %s", source_record_id)
                existing.mark_skipped()
                self.store.save_state(existing)
                run.records_skipped += 1
                return

            # Create or update state
            state = existing or SyncState(
                tenant_id=self.tenant_id,
                source_connector=self.source.name,
                source_record_id=source_record_id,
                target_connector=self.target.name,
                content_hash=unified.content_hash or "",
            )
            state.content_hash = unified.content_hash or ""

            # Export with retry
            result = await self.export(unified, state)

            # Update state based on result
            if result.is_success:
                if result.status == ExportResult.Status.SKIPPED_UNCHANGED:
                    state.mark_skipped()
                    run.records_skipped += 1
                else:
                    state.mark_exported(result.target_id or "")
                    run.records_succeeded += 1
            else:
                error_msg = result.error.message if result.error else "Unknown error"
                state.mark_failed(error_msg)
                run.records_failed += 1

            self.store.save_state(state)

        except Exception as e:
            logger.error("Failed to process invoice %s: %s", source_record_id, e)
            state = SyncState(
                tenant_id=self.tenant_id,
                source_connector=self.source.name,
                source_record_id=source_record_id,
                target_connector=self.target.name,
                content_hash="",
            )
            state.mark_failed(str(e))
            self.store.save_state(state)
            run.records_failed += 1

    async def export(
        self,
        invoice: UnifiedInvoice,
        state: SyncState,
    ) -> ExportResult:
        """
        Export invoice to target with retry logic.

        Uses exponential backoff for retryable errors and rate limits.
        """
        idempotency_key = self._build_idempotency_key(invoice)

        for attempt in range(self.max_retries + 1):
            try:
                # Decide: update existing or create new
                if (
                    state.target_record_id
                    and self.target.supports_update
                    and state.status == SyncStateStatus.EXPORTED
                ):
                    result = await self.target.update_invoice(
                        state.target_record_id,
                        invoice,
                    )
                else:
                    result = await self.target.export_invoice(
                        invoice,
                        idempotency_key,
                    )

                # If success or permanent error, return immediately
                if result.is_success or not result.is_retryable:
                    return result

                # Retryable error — wait and retry
                if attempt < self.max_retries:
                    delay = self.retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "Retryable error exporting %s (attempt %d/%d), retrying in %.1fs: %s",
                        invoice.external_id,
                        attempt + 1,
                        self.max_retries + 1,
                        delay,
                        result.error.message if result.error else "unknown",
                    )
                    await asyncio.sleep(delay)

            except RateLimitError as e:
                if attempt < self.max_retries:
                    delay = e.retry_after_seconds or (self.retry_base_delay * (2 ** attempt))
                    logger.warning(
                        "Rate limited exporting %s (attempt %d/%d), waiting %.1fs",
                        invoice.external_id,
                        attempt + 1,
                        self.max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    return ExportResult(
                        status=ExportResult.Status.CREATED,
                        error=SyncError(
                            category=ErrorCategory.RETRYABLE,
                            code="RATE_LIMITED",
                            message=f"Rate limited after {self.max_retries + 1} attempts",
                            retry_after_seconds=e.retry_after_seconds,
                        ),
                    )

        # Exhausted all retries
        return ExportResult(
            status=ExportResult.Status.CREATED,
            error=SyncError(
                category=ErrorCategory.RETRYABLE,
                code="MAX_RETRIES",
                message=f"Failed after {self.max_retries + 1} attempts",
            ),
        )

    def _build_idempotency_key(self, invoice: UnifiedInvoice) -> str:
        """
        Build idempotency key for target export.

        Format: tenant:source:source_id:content_hash
        This ensures:
        - Same invoice from same tenant+source is idempotent
        - Changed invoice gets a new key (triggers update)
        """
        return f"{self.tenant_id}:{self.source.name}:{invoice.external_id}:{invoice.content_hash}"

    async def replay_failed(self, run_id: str) -> SyncRun:
        """
        Replay failed records from a previous sync run.

        Creates a new sync run that only processes failed records.
        """
        original_run = self.store.get_run(run_id)
        if not original_run:
            raise ValueError(f"Sync run {run_id} not found")

        failed_states = self.store.get_states_by_status(
            self.tenant_id,
            SyncStateStatus.FAILED,
        )

        logger.info(
            "Replaying %d failed records from run %s",
            len(failed_states),
            run_id,
        )

        run = SyncRun(
            tenant_id=self.tenant_id,
            source_connector=self.source.name,
            target_connector=self.target.name,
        )
        self.store.create_run(run)

        for state in failed_states:
            run.records_processed += 1
            try:
                # TODO: Re-fetch from source by ID instead of using placeholder
                result = await self.export(
                    UnifiedInvoice(
                        external_id=state.source_record_id,
                        invoice_number="",
                        customer={"external_id": ""},
                        currency="USD",
                        total=0,
                        tax_total=0,
                        status="draft",
                        issue_date="2024-01-01",
                    ),
                    state,
                )

                if result.is_success:
                    state.mark_exported(result.target_id or "")
                    run.records_succeeded += 1
                else:
                    state.mark_failed(
                        result.error.message if result.error else "Unknown"
                    )
                    run.records_failed += 1

                self.store.save_state(state)

            except Exception as e:
                state.mark_failed(str(e))
                self.store.save_state(state)
                run.records_failed += 1

        run.complete()
        self.store.update_run(run)
        return run
