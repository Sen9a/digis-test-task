from enum import Enum


class ErrorCategory(str, Enum):
    """Classification of sync errors for appropriate handling."""

    RETRYABLE = "retryable"  # 429, 503, network timeout — retry with backoff
    PERMANENT = "permanent"  # 400 validation, 404 not found — needs human/fix
    CONFLICT = "conflict"  # 409 duplicate, 422 already exists — resolve via state
    AUTH = "auth"  # 401, 403 — pause tenant, alert operator
