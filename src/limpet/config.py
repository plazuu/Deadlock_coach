"""User configuration.

Resolution order (highest priority first):
  1. ``LIMPET_*`` environment variables
  2. ``<data_dir>/config.json`` (written by ``limpet init``)
  3. defaults defined here

Secrets (API keys) may live in the JSON file, but env vars are preferred. The
Anthropic key is optional here: if unset, the Anthropic SDK's own resolution
(``ANTHROPIC_API_KEY`` / ``ant auth`` profile) applies.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from . import paths
from .ids import to_account_id


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIMPET_", extra="ignore")

    # Identity — accepts any Steam ID form; normalised to a 32-bit account id.
    steam_id: str = Field(default="", description="Your Steam ID (any form) or account id")

    # Credentials
    deadlock_api_key: str = Field(default="", description="deadlock-api.com X-API-KEY (optional)")
    anthropic_api_key: str = Field(default="", description="Anthropic API key (optional)")

    # Coaching
    model: str = Field(default="claude-opus-5")
    briefing_token_budget: int = Field(default=12_000)

    # Auto-watch
    poll_interval_minutes: int = Field(default=10)
    match_modes: list[str] = Field(default_factory=lambda: ["ranked", "unranked"])

    @property
    def account_id(self) -> int:
        if not self.steam_id:
            raise ValueError("No steam_id configured. Run `limpet init` first.")
        return to_account_id(self.steam_id)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        json_source = JsonConfigSettingsSource(settings_cls, json_file=paths.config_path())
        return (init_settings, env_settings, json_source)


def load_settings() -> Settings:
    return Settings()


def write_config(values: dict[str, Any]) -> None:
    """Persist a subset of settings to ``config.json`` (merging with any existing file)."""
    path = paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.exists():
        current = json.loads(path.read_text())
    current.update({k: v for k, v in values.items() if v not in (None, "")})
    path.write_text(json.dumps(current, indent=2) + "\n")
    path.chmod(0o600)
