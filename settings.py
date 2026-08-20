from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # API URLs
    source_api_url: str = "http://localhost:8001"
    target_api_url: str = "http://localhost:8002"

    # Auth
    source_api_key: str = "test-key"
    target_api_key: str = "test-key"

    # Tenant
    tenant_id: str = "demo-tenant"

    # Sync behavior
    batch_size: int = 10
    max_retries: int = 3
    retry_base_delay: float = 1.0

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

settings = Settings()