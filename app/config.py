from functools import lru_cache
from urllib.parse import quote_plus, urlparse

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
    expected_source_id: str = ""
    switch_field_id: int = 1631120
    message_field_id: int = 1890488
    salesbot_id: int = 86970

    buffer_window_seconds: float = 3.0
    buffer_max_messages: int = 8
    dedupe_ttl_seconds: int = 900

    kommo_mcp_url: str = "https://tgvfvsruvfzrmfohbgwx.supabase.co/functions/v1/kommo-mcp"
    kommo_mcp_api_key: str | None = None

    supabase_url: str = "https://tgvfvsruvfzrmfohbgwx.supabase.co"
    supabase_service_key: str | None = None
    rag_embedding_model: str = "text-embedding-3-small"
    rag_match_threshold: float = 0.45
    rag_match_count: int = 5

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    llm_temperature: float = 0.5
    llm_max_output_tokens: int = 500

    webhook_shared_secret: str | None = None

    # Pipeline filter — only respond to leads in these pipeline IDs.
    # Default: Call center (12372259). Empty list = respond to all.
    allowed_pipeline_ids: list[int] = [12372259]
    # Skip leads in closed statuses (142=won, 143=lost)
    skip_closed_statuses: bool = True
    # Phone whitelist for testing — only these numbers trigger the agent.
    # Empty list = respond to all. Format: ["573204549502"]
    test_phone_whitelist: list[str] = ["573204549502"]

    request_timeout_seconds: int = 25
    startup_validate_integrations: bool = True
    supabase_db_url: str | None = None
    supabase_db_password: str | None = None
    supabase_db_user: str = "postgres"
    supabase_db_name: str = "postgres"
    # Full pooler host override — set this if auto-detection fails.
    # Example: aws-1-us-east-1.pooler.supabase.com
    supabase_db_pooler_host: str = "aws-1-us-east-1.pooler.supabase.com"

    def resolved_supabase_db_url(self) -> str | None:
        explicit = (self.supabase_db_url or "").strip()
        if explicit:
            return explicit

        password = (self.supabase_db_password or "").strip()
        if not password:
            return None

        project_ref = self._infer_project_ref()
        if not project_ref:
            return None

        # Use Supabase Session Pooler (IPv4) — direct connection resolves
        # to IPv6 which fails on many VPS providers (Hostinger, etc.)
        safe_password = quote_plus(password)
        pooler_host = self.supabase_db_pooler_host
        pooler_user = f"postgres.{project_ref}"
        return (
            f"postgresql://{pooler_user}:{safe_password}"
            f"@{pooler_host}:5432/{self.supabase_db_name}?sslmode=require"
        )

    def _infer_project_ref(self) -> str | None:
        """Extract Supabase project reference from known URLs."""
        # Try from supabase_url first, then kommo_mcp_url
        for url in (self.supabase_url, self.kommo_mcp_url):
            parsed = urlparse(url)
            netloc = parsed.netloc.strip().lower()
            if not netloc:
                continue
            parts = netloc.split(".")
            if len(parts) >= 3 and parts[0]:
                return parts[0]
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
