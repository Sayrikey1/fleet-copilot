"""Central config — one memoized settings object; thresholds are the single source of truth."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLEET_", env_file=".env", extra="ignore")

    # LLM (live runs only); key accepts ANTHROPIC_API_KEY or CLAUDE_API_KEY.
    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    )
    model: str = "claude-sonnet-5-5"
    temperature: float | None = None  # None => model default (reasoning models reject others)
    max_tool_rounds: int = 6

    # Data-layer paths, relative to the working directory.
    dataset_path: Path = Path("data/device-telemetry-dataset.ndjson")
    db_path: Path = Path("fleet.db")
    audit_jsonl_path: Path = Path("audit_log.jsonl")

    # Grounding thresholds (shared with the golden eval set).
    disk_low_free_pct: float = 10.0
    ram_high_used_pct: float = 80.0
    battery_eol_cycles: int = 1000
    battery_service_conditions: tuple[str, ...] = ("Service Recommended", "Replace Soon", "Service Battery")
    macos_floor_major: int = 15

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
