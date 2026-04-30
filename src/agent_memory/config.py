"""Configuration loading for Agent Memory vaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_memory.schema import LifecycleStatus, MemoryScope, SCHEMA_VERSION

CONFIG_DIR_NAME = ".agent-memory"
CONFIG_FILE_NAME = "config.yaml"
ENV_VAULT_PATH = "AGENT_MEMORY_VAULT"


class ConfigError(ValueError):
    """Raised when a vault config cannot be found or loaded."""


class MemoryConfig(BaseModel):
    """Stage 2 configuration for a local Agent Memory vault."""

    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True)

    schema_version: int = SCHEMA_VERSION
    vault_path: Path
    memories_dir: str = "Memories"
    sources_dir: str = "Sources"
    briefs_dir: str = "Briefs"
    profiles_dir: str = "Profiles"
    synthesis_dir: str = "Synthesis"
    agent_memory_dir: str = CONFIG_DIR_NAME
    index_path: str = ".agent-memory/index.sqlite"
    default_scope: MemoryScope = MemoryScope.USER
    default_project: Optional[str] = None
    user_default_status: LifecycleStatus = LifecycleStatus.ACTIVE
    agent_default_status: LifecycleStatus = LifecycleStatus.PENDING
    default_author_name: str = "memory CLI"

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        return value

    @field_validator(
        "memories_dir",
        "sources_dir",
        "briefs_dir",
        "profiles_dir",
        "synthesis_dir",
        "agent_memory_dir",
        "index_path",
        "default_author_name",
    )
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("config string values must not be empty")
        return value

    @property
    def config_path(self) -> Path:
        return self.vault_path / self.agent_memory_dir / CONFIG_FILE_NAME

    @property
    def memory_root(self) -> Path:
        return self.vault_path / self.memories_dir

    @property
    def index_file(self) -> Path:
        return self.vault_path / self.index_path


def create_default_config(vault_path: Union[Path, str]) -> MemoryConfig:
    """Create the default Stage 2 config model for a vault path."""

    return MemoryConfig(vault_path=Path(vault_path).expanduser().resolve())


def write_config(config: MemoryConfig, *, overwrite: bool = False) -> bool:
    """Write `.agent-memory/config.yaml`.

    Returns True when a file was written and False when an existing config was
    preserved.
    """

    config_path = config.config_path
    if config_path.exists() and not overwrite:
        return False

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_data = config.model_dump(mode="json", exclude={"vault_path"})
    config_path.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
    return True


def load_config(
    vault_path: Optional[Union[Path, str]] = None,
    *,
    start_path: Optional[Union[Path, str]] = None,
) -> MemoryConfig:
    """Load vault config from an explicit vault, env var, or nearest parent."""

    resolved_vault = _resolve_vault_path(vault_path, start_path=start_path)
    config_path = resolved_vault / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    if not config_path.exists():
        raise ConfigError(f"config not found at {config_path}")

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError("config must be a YAML mapping")

    try:
        return MemoryConfig.model_validate({**loaded, "vault_path": resolved_vault})
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def find_config_path(start_path: Optional[Union[Path, str]] = None) -> Optional[Path]:
    """Find the nearest parent directory containing `.agent-memory/config.yaml`."""

    current = Path(start_path or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        config_path = candidate / CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if config_path.exists():
            return config_path
    return None


def _resolve_vault_path(
    vault_path: Optional[Union[Path, str]],
    *,
    start_path: Optional[Union[Path, str]] = None,
) -> Path:
    if vault_path is not None:
        return Path(vault_path).expanduser().resolve()

    env_vault = os.environ.get(ENV_VAULT_PATH)
    if env_vault:
        return Path(env_vault).expanduser().resolve()

    config_path = find_config_path(start_path=start_path)
    if config_path:
        return config_path.parent.parent

    raise ConfigError(
        f"could not find {CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}; pass --vault or set {ENV_VAULT_PATH}"
    )


def config_to_dict(config: MemoryConfig) -> dict[str, Any]:
    """Return a JSON-safe config summary."""

    data = config.model_dump(mode="json")
    data["vault_path"] = str(config.vault_path)
    data["config_path"] = str(config.config_path)
    return data
