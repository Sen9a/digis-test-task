from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from src.services import SyncRunService, SyncStateService
from src.abstract import SourceConnector, TargetConnector
from exceptions import (
    AuthenticationError,
    RateLimitError,
    RetryableExportError,
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
from settings import settings

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
    sync_run_service: SyncRunService
    sync_state_service: SyncStateService
    tenant_id: str
    logger: 'logging.getLogger'

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
        run = await self.sync_run_service.create_run(tenant_id=self.tenant_id,
                                                     source_connector=self.source.name,
                                                     target_connector=self.target.name)
        try:
            await self._authenticate(source_credentials, target_credentials)

            async for raw in self.source.fetch_all_invoices(
                cursor=cursor,
                batch_size=batch_size,
            ):
                run.records_processed += 1
                await self._process_invoice(raw, run)

            run.complete()
            self.logger.info(
                "Sync run %s completed: %d processed, %d succeeded, %d failed, %d skipped",
                run.id,
                run.records_processed,
                run.records_succeeded,
                run.records_failed,
                run.records_skipped,
            )

        except (AuthenticationError, SourceUnavailableError) as e:
            self.logger.error("Sync run %s failed: %s", run.id, e)
            run.fail()
        except Exception:
            self.logger.exception("Sync run %s failed unexpectedly", run.id)
            run.fail()

        await self.sync_run_service.update_run(run)
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
            existing = await self.sync_state_service.get_state(
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
                self.logger.debug("Skipping unchanged invoice %s", source_record_id)
                existing.mark_skipped()
                await self.sync_state_service.save_state(existing)
                run.records_skipped += 1
                return

            # Create or update state
            state = existing or await self.sync_state_service.sync_state(
                tenant_id=self.tenant_id,
                source_name=self.source.name,
                source_record_id=source_record_id,
                target_name=self.target.name,
                content_hash=unified.content_hash
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

            await self.sync_state_service.save_state(state)

        except Exception as e:
            self.logger.error("Failed to process invoice %s: %s", source_record_id, e)
            state = await self.sync_state_service.sync_state(
                tenant_id=self.tenant_id,
                source_name=self.source.name,
                source_record_id=source_record_id,
                target_name=self.target.name,
                content_hash="",
            )
            state.mark_failed(str(e))
            await self.sync_state_service.save_state(state)
            run.records_failed += 1

    @retry(
        stop=stop_after_attempt(settings.max_retries + 1),
        wait=wait_exponential(multiplier=settings.retry_base_delay,
                              min=settings.retry_base_delay,
                              max=60),
        retry=retry_if_exception_type((RateLimitError, RetryableExportError)),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    async def _do_export(
        self,
        invoice: UnifiedInvoice,
        state: SyncState,
    ) -> ExportResult:
        """
        Single export attempt with tenacity retry.

        Raises:
            RateLimitError: On 429 rate limit.
            RetryableExportError: On retryable ExportResult failure.

        Returns:
            ExportResult on success or permanent (non-retryable) failure.
        """
        idempotency_key = self._build_idempotency_key(invoice)

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

        if not result.is_success and result.is_retryable:
            raise RetryableExportError(result)

        return result

    async def export(
        self,
        invoice: UnifiedInvoice,
        state: SyncState,
    ) -> ExportResult:
        """
        Export invoice to target.

        Calls _do_export which has tenacity retry built in.
        Catches final failures and converts to ExportResult.
        """
        try:
            return await self._do_export(invoice, state)
        except RetryableExportError as e:
            return e.result
        except RateLimitError as e:
            return ExportResult(
                status=ExportResult.Status.CREATED,
                error=SyncError(
                    category=ErrorCategory.RETRYABLE,
                    code="RATE_LIMITED",
                    message=f"Rate limited after {settings.max_retries + 1} attempts",
                    retry_after_seconds=e.retry_after_seconds,
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
        original_run = await self.sync_run_service.get_run(run_id)
        if not original_run:
            raise ValueError(f"Sync run {run_id} not found")

        failed_states = await self.sync_state_service.get_states_by_status(
            self.tenant_id,
            SyncStateStatus.FAILED,
        )

        self.logger.info(
            "Replaying %d failed records from run %s",
            len(failed_states),
            run_id,
        )

        run = await self.sync_run_service.create_run(tenant_id=self.tenant_id,
                                                     source_connector=self.source.name,
                                                     target_connector=self.target.name)

        for state in failed_states:
            run.records_processed += 1
            try:
                # Re-fetch the real invoice from source
                raw = await self.source.fetch_invoice_by_id(state.source_record_id)
                unified = self.source.normalize(raw)
                unified = unified.with_content_hash()
                state.content_hash = unified.content_hash or ""

                result = await self.export(unified, state)

                if result.is_success:
                    state.mark_exported(result.target_id or "")
                    run.records_succeeded += 1
                else:
                    state.mark_failed(
                        result.error.message if result.error else "Unknown"
                    )
                    run.records_failed += 1

                await self.sync_state_service.save_state(state)

            except Exception as e:
                state.mark_failed(str(e))
                await self.sync_state_service.save_state(state)
                run.records_failed += 1

        run.complete()
        await self.sync_run_service.update_run(run)
        return run
