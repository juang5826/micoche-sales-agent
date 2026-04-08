from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "micoche-sales-agent"
    environment: str = "production"
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    tenant_key: str = "micoche"
    expected_subdomain: str = "auxcontablemicoche"
    expected_source_id: str = "573174274959"
    switch_field_id: int = 1631120
    message_field_id: int = 1890488
    salesbot_id: int = 86970

    buffer_window_seconds: float = 3.0
    buffer_max_messages: int = 8
    dedupe_ttl_seconds: int = 900

    kommo_mcp_url: str = "https://tgvfvsruvfzrmfohbgwx.supabase.co/functions/v1/kommo-mcp"
    kommo_mcp_api_key: str | None = None

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    llm_temperature: float = 0.2
    llm_max_output_tokens: int = 500

    request_timeout_seconds: int = 25
    startup_validate_integrations: bool = True
    supabase_db_url: str | None = None
    supabase_db_password: str | None = None
    supabase_db_user: str = "postgres"
    supabase_db_name: str = "postgres"

    def resolved_supabase_db_url(self) -> str | None:
        explicit = (self.supabase_db_url or "").strip()
        if explicit:
            return explicit

        password = (self.supabase_db_password or "").strip()
        if not password:
            return None

        host = self._infer_supabase_db_host_from_mcp()
        if not host:
            return None
        return (
            f"postgresql://{self.supabase_db_user}:{password}"
            f"@{host}:5432/{self.supabase_db_name}?sslmode=require"
        )

    def _infer_supabase_db_host_from_mcp(self) -> str | None:
        parsed = urlparse(self.kommo_mcp_url)
        netloc = parsed.netloc.strip().lower()
        if not netloc:
            return None
        parts = netloc.split(".")
        if len(parts) < 3:
            return None
        project_ref = parts[0]
        if not project_ref:
            return None
        return f"db.{project_ref}.supabase.co"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
