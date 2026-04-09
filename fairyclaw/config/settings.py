# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Application settings definitions."""

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Central runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="FAIRYCLAW_", env_file=("config/fairyclaw.env", ".env"), extra="ignore")

    api_token: str = "sk-fairyclaw-dev-token"
    database_url: str = "sqlite+aiosqlite:///./data/fairyclaw.db"
    data_dir: str = "./data"
    host: str = "0.0.0.0"
    port: int = 8000
    llm_endpoints_config_path: str = str(PROJECT_ROOT / "config/llm_endpoints.yaml")
    filesystem_root_dir: str | None = None
    log_level: str = "INFO"
    log_file_path: str = "./data/logs/fairyclaw.log"
    log_to_stdout: bool = False
    capabilities_dir: str = str(PROJECT_ROOT / "fairyclaw/capabilities")
    execution_timeout_seconds: int = 30
    web_proxy: str | None = None
    event_bus_worker_count: int = 2
    planner_heartbeat_seconds: int = 15
    planner_wakeup_debounce_ms: int = 200
    router_profile_name: str = "router"
    sqlite_busy_timeout_seconds: float = 8.0
    db_write_retry_attempts: int = 3
    db_write_retry_base_delay_seconds: float = 0.1
    hook_default_timeout_ms: int = 300
    context_token_budget: int = 0
    enable_hook_runtime: bool = False
    reins_enabled: bool = False
    reins_budget_daily_usd: float = 100.0
    reins_on_exceed: str = "reject"
    enable_file_upload_event: bool = False
    # Deprecated: kept for backward compatibility with previous naming.
    enable_rag_pipeline: bool = False
    bridge_token: str = "fairyclaw-bridge-dev-token"
    bridge_ws_path: str = "/internal/gateway/ws"
    bridge_outbound_backlog_size: int = 512
    bridge_max_inflight_per_session: int = 8
    bridge_max_file_bytes: int = 25 * 1024 * 1024
    bridge_max_chunk_bytes: int = 256 * 1024
    bridge_max_inflight_file_transfers: int = 4
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8081
    gateway_id: str = "gw_local"
    gateway_bridge_url: str = "ws://127.0.0.1:8000/internal/gateway/ws"
    gateway_reconnect_seconds: float = 1.0
    # OneBot: read from same env files as FAIRYCLAW_*; supports unprefixed ONEBOT_* (not visible to raw os.getenv).
    onebot_session_cmd_prefix: str = Field(
        default="/sess",
        validation_alias=AliasChoices(
            "ONEBOT_SESSION_CMD_PREFIX",
            "FAIRYCLAW_ONEBOT_SESSION_CMD_PREFIX",
        ),
    )

    @field_validator("llm_endpoints_config_path", "capabilities_dir", "log_file_path", "data_dir", "filesystem_root_dir")
    @classmethod
    def resolve_path(cls, v: str | None) -> str | None:
        """Resolve configured path relative to project root.

        Args:
            v (str | None): Raw path value from configuration.

        Returns:
            str | None: Absolute normalized path or None.
        """
        if v is None:
            return None
        path = Path(v)
        if not path.is_absolute():
            return str(PROJECT_ROOT / path)
        return v

    def ensure_dirs(self) -> None:
        """Ensure required runtime directories exist on local filesystem.

        Returns:
            None
        """
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.data_dir, "files").mkdir(parents=True, exist_ok=True)
        Path(self.data_dir, "logs").mkdir(parents=True, exist_ok=True)


settings = Settings()
