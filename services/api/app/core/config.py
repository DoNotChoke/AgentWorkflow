"""Cấu hình ứng dụng, đọc từ biến môi trường (tiền tố ``AW_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── Chung ───────────────────────────────────────────────────────────────
    environment: Literal["local", "dev", "staging", "production"] = "local"
    debug: bool = False
    service_name: str = "agentworkflow-api"

    # ─── PostgreSQL ───────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://agentworkflow:agentworkflow@localhost:5432/agentworkflow"
    )
    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_echo: bool = False

    # ─── Redis ────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20
    redis_socket_timeout_seconds: float = 2.0

    # ─── Object storage ───────────────────────────────────────────────
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "minio"
    s3_secret_access_key: SecretStr = SecretStr("minio123")
    s3_bucket_traces: str = "aw-traces"
    s3_bucket_attachments: str = "aw-attachments"
    s3_bucket_artifacts: str = "aw-artifacts"

    # ─── Temporal ──────────────────────────────────────────────
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue_integration_read: str = "integration-read"
    temporal_task_queue_integration_write: str = "integration-write"
    temporal_task_queue_ai: str = "ai-tasks"
    temporal_task_queue_processing: str = "data-processing"

    # ─── Observability ───────────────────────────────────────────────
    otel_enabled: bool = True
    otel_exporter_endpoint: str = "http://localhost:4317"
    log_level: str = "INFO"
    log_json: bool = True

    # ─── Bảo mật ───────────────────────────────────────────────────────
    jwt_secret: SecretStr = SecretStr("thay-doi-truoc-khi-dung-that")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agentworkflow"
    access_token_ttl_seconds: int = 3600

    refresh_token_ttl_seconds: int = 30 * 24 * 3600

    # ─── Cookie chứa refresh token ────────────────────────────────────
    refresh_cookie_name: str = "aw_refresh"  # noqa: S105 — tên cookie, không phải bí mật
    #: Giới hạn phạm vi gửi cookie, giảm bề mặt CSRF.
    refresh_cookie_path: str = "/v1/auth"
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    refresh_cookie_domain: str | None = None

    kms_provider: Literal["local", "aws", "vault"] = "local"
    kms_local_master_key: SecretStr = SecretStr("thay-doi-truoc-khi-dung-that")

    # ─── Model Gateway ────────────────────────────────────────────────
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_default_model: str = "claude-sonnet-5"

    openai_api_key: SecretStr = SecretStr("")
    openai_default_model: str = "gpt-5.4-mini"

    # ─── Tiện ích ────────────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def refresh_cookie_secure(self) -> bool:
        """Cookie chỉ đi qua HTTPS, trừ khi đang chạy cục bộ."""
        return self.environment != "local"

    def assert_production_safe(self) -> None:
        """Chặn khởi động production với giá trị mặc định của môi trường dev."""
        if not self.is_production:
            return

        placeholder = "thay-doi-truoc-khi-dung-that"
        problems: list[str] = []
        if self.jwt_secret.get_secret_value() == placeholder:
            problems.append("AW_JWT_SECRET vẫn là giá trị mặc định")
        elif len(self.jwt_secret.get_secret_value().encode()) < 32:
            problems.append("AW_JWT_SECRET ngắn hơn 32 byte (RFC 7518 §3.2)")
        if self.kms_local_master_key.get_secret_value() == placeholder:
            problems.append("AW_KMS_LOCAL_MASTER_KEY vẫn là giá trị mặc định")
        if self.kms_provider == "local":
            problems.append("AW_KMS_PROVIDER=local không được phép ở production (§21)")
        if self.debug:
            problems.append("AW_DEBUG=true không được phép ở production")

        if problems:
            raise RuntimeError("Cấu hình production không an toàn: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
