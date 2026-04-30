"""Configuration loading for Agent Memory vaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_memory.schema import LifecycleStatus, MemoryScope, MemoryType, SCHEMA_VERSION

CONFIG_DIR_NAME = ".agent-memory"
CONFIG_FILE_NAME = "config.yaml"
ENV_VAULT_PATH = "AGENT_MEMORY_VAULT"
ENV_SEMANTIC_PROVIDER = "AGENT_MEMORY_SEMANTIC_PROVIDER"
ENV_SEMANTIC_MODEL = "AGENT_MEMORY_SEMANTIC_MODEL"
ENV_SEMANTIC_BATCH_SIZE = "AGENT_MEMORY_SEMANTIC_BATCH_SIZE"
ENV_SEMANTIC_DIMENSIONS = "AGENT_MEMORY_SEMANTIC_DIMENSIONS"
ENV_SEMANTIC_MIN_SIMILARITY = "AGENT_MEMORY_SEMANTIC_MIN_SIMILARITY"
ENV_FRESHNESS_ENABLED = "AGENT_MEMORY_FRESHNESS_ENABLED"
ENV_FRESHNESS_INTERVAL_SECONDS = "AGENT_MEMORY_FRESHNESS_INTERVAL_SECONDS"
ENV_FRESHNESS_DEBOUNCE_SECONDS = "AGENT_MEMORY_FRESHNESS_DEBOUNCE_SECONDS"
ENV_FRESHNESS_CLEAN = "AGENT_MEMORY_FRESHNESS_CLEAN"


class ConfigError(ValueError):
    """Raised when a vault config cannot be found or loaded."""


class SemanticConfig(BaseModel):
    """Optional semantic search configuration."""

    provider: Optional[str] = None
    model: str = "local-embedding-model"
    command: Optional[list[str]] = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    batch_size: int = Field(default=32, ge=1)
    dimensions: Optional[int] = Field(default=None, ge=1)
    min_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    vector_limit: int = Field(default=100, ge=1)
    keyword_limit: int = Field(default=100, ge=1)

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("model")
    @classmethod
    def require_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("semantic model must not be empty")
        return value.strip()

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        return cleaned or None


class RecallConfig(BaseModel):
    """Deterministic recall packing limits."""

    candidate_limit: int = Field(default=50, ge=1)
    max_tokens_per_chunk: int = Field(default=300, ge=1)
    max_chunks_per_document: int = Field(default=2, ge=1)
    max_chunks_per_project: int = Field(default=8, ge=1)
    max_chunks_per_memory_type: dict[str, int] = Field(
        default_factory=lambda: {
            MemoryType.PREFERENCE.value: 4,
            MemoryType.DECISION.value: 6,
            MemoryType.PROJECT_CONTEXT.value: 6,
            MemoryType.FACT.value: 8,
            MemoryType.TASK.value: 4,
            MemoryType.SOURCE_EXTRACT.value: 3,
            MemoryType.CONVERSATION_SUMMARY.value: 3,
        }
    )

    @field_validator("max_chunks_per_memory_type")
    @classmethod
    def validate_memory_type_caps(cls, value: dict[str, int]) -> dict[str, int]:
        valid_types = {memory_type.value for memory_type in MemoryType}
        cleaned: dict[str, int] = {}
        for key, cap in value.items():
            memory_type = MemoryType(key).value if key in valid_types else key
            if memory_type not in valid_types:
                raise ValueError(f"unknown memory type cap: {key}")
            if int(cap) < 1:
                raise ValueError("memory type caps must be at least 1")
            cleaned[memory_type] = int(cap)
        return cleaned


class IndexFreshnessConfig(BaseModel):
    """Polling freshness settings for the local service."""

    enabled: bool = True
    interval_seconds: int = Field(default=30, ge=1)
    debounce_seconds: float = Field(default=2.0, ge=0)
    clean: bool = False


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
    semantic: SemanticConfig = Field(default_factory=SemanticConfig)
    recall: RecallConfig = Field(default_factory=RecallConfig)
    index_freshness: IndexFreshnessConfig = Field(default_factory=IndexFreshnessConfig)

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
        return MemoryConfig.model_validate(_apply_environment_overrides({**loaded, "vault_path": resolved_vault}))
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


def _apply_environment_overrides(config_data: dict[str, Any]) -> dict[str, Any]:
    semantic_overrides: dict[str, Any] = {}
    for env_name, field_name in (
        (ENV_SEMANTIC_PROVIDER, "provider"),
        (ENV_SEMANTIC_MODEL, "model"),
        (ENV_SEMANTIC_BATCH_SIZE, "batch_size"),
        (ENV_SEMANTIC_DIMENSIONS, "dimensions"),
        (ENV_SEMANTIC_MIN_SIMILARITY, "min_similarity"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            semantic_overrides[field_name] = value

    freshness_overrides: dict[str, Any] = {}
    for env_name, field_name in (
        (ENV_FRESHNESS_ENABLED, "enabled"),
        (ENV_FRESHNESS_INTERVAL_SECONDS, "interval_seconds"),
        (ENV_FRESHNESS_DEBOUNCE_SECONDS, "debounce_seconds"),
        (ENV_FRESHNESS_CLEAN, "clean"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            freshness_overrides[field_name] = value

    if not semantic_overrides and not freshness_overrides:
        return config_data

    semantic_config = config_data.get("semantic") or {}
    if not isinstance(semantic_config, dict):
        semantic_config = {}
    freshness_config = config_data.get("index_freshness") or {}
    if not isinstance(freshness_config, dict):
        freshness_config = {}
    return {
        **config_data,
        "semantic": {
            **semantic_config,
            **semantic_overrides,
        },
        "index_freshness": {
            **freshness_config,
            **freshness_overrides,
        },
    }


def config_to_dict(config: MemoryConfig) -> dict[str, Any]:
    """Return a JSON-safe config summary."""

    data = config.model_dump(mode="json")
    data["vault_path"] = str(config.vault_path)
    data["config_path"] = str(config.config_path)
    return data
