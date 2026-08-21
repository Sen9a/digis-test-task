from exceptions import RateLimitError
from settings import settings


def wait_retry_after_aware(retry_state) -> float:
    """
    Wait strategy: honor the server's Retry-After when rate limited,
    exponential backoff (base * 2^n, capped at 60s) otherwise.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RateLimitError) and exc.retry_after_seconds is not None:
        return min(float(exc.retry_after_seconds), 60.0)
    return min(
        settings.retry_base_delay * (2 ** (retry_state.attempt_number - 1)),
        60.0,
    )