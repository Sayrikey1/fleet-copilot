"""Central configuration — one memoized settings object (pattern from SpinStrip-AI).

Every field has a working default so the app and the deterministic test suite boot
with *no* environment at all; only live LLM runs need ``ANTHROPIC_API_KEY``.
Thresholds live here (not scattered in tools) so the golden eval set and the tools
read the exact same numbers — the single source of truth for "what counts as low".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (src/fleet_copilot/settings.py -> repo/).
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEET_",
        env_file=".env",  # relative to the working directory (the repo root)
        extra="ignore",
    )

    # --- LLM (live runs only) ---
    # Accepts either ANTHROPIC_API_KEY or CLAUDE_API_KEY (no FLEET_ prefix).
    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    )
    model: str = "claude-sonnet-5-5"
    # None => use the model's default (Claude reasoning models only accept their default).
    temperature: float | None = None
    max_tool_rounds: int = 6

    # --- Data layer (paths are relative to the current working directory, i.e. the repo
    #     root you run commands from; override any of them via FLEET_* env vars) ---
    dataset_path: Path = Path("data/device-telemetry-dataset.ndjson")
    db_path: Path = Path("fleet.db")
    audit_jsonl_path: Path = Path("audit_log.jsonl")

    # --- Grounding thresholds (single source of truth; chosen at natural data gaps) ---
    # Disk: a device is "low on disk" when its root-volume free% is at/below this.
    disk_low_free_pct: float = 10.0
    # Memory: "RAM constrained" when mean used% is at/above this.
    ram_high_used_pct: float = 80.0
    # Battery: "approaching end-of-life" when cycle_count >= this OR condition flags service.
    battery_eol_cycles: int = 1000
    battery_service_conditions: tuple[str, ...] = ("Service Recommended", "Replace Soon", "Service Battery")
    # OS: the macOS major version below which a device is "older than macOS N".
    macos_floor_major: int = 15

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Memoized singleton so every module sees the same config."""
    return Settings()
