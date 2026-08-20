from enum import Enum


class ErrorCategory(str, Enum):
    """Classification of sync errors for appropriate handling."""

    RETRYABLE = "retryable"  # 429, 503, network timeout — retry with backoff
    PERMANENT = "permanent"  # 400 validation, 404 not found — needs human/fix
    CONFLICT = "conflict"  # 409 duplicate, 422 already exists — resolve via state
    AUTH = "auth"  # 401, 403 — pause tenant, alert operator


class Status(str, Enum):
    CREATED = "created"  # New record created in target
    UPDATED = "updated"  # Existing record updated
    SKIPPED_UNCHANGED = "skipped_unchanged"  # No changes detected
    ALREADY_EXISTS = "already_exists"  # Duplicate detected, resolved
    REVERSED_AND_RECREATED = "reversed_and_recreated"  # Old reversed, new created
    FAILED = "failed"  # Export failed; see error for category/details