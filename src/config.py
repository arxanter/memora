"""Configuration loading for managed Memora homes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from schema import MemoryType, SCHEMA_VERSION

CONFIG_FILE_NAME = "config.yaml"
DEFAULT_HOME_DIR_NAME = "memora"
DEFAULT_VAULT_DIR_NAME = "vault"
DEFAULT_RAW_DIR = "raw"
DEFAULT_MEMORIES_DIR = "Memories"
DEFAULT_SOURCES_DIR = "Sources"
DEFAULT_WIKI_DIR = "Wiki"
DEFAULT_STATE_DIR_NAME = "state"
DEFAULT_INDEX_PATH = "state/index.sqlite"
DEFAULT_USER_RECALL_BUDGET = 500
DEFAULT_PROJECT_RECALL_BUDGET = 700
DEFAULT_SEMANTIC_TIMEOUT_SECONDS = 30.0
DEFAULT_SEMANTIC_BATCH_SIZE = 32
DEFAULT_SEMANTIC_DETERMINISTIC_DIMENSIONS = 32
DEFAULT_SEMANTIC_MIN_SIMILARITY = 0.0
DEFAULT_SEMANTIC_VECTOR_LIMIT = 100
DEFAULT_SEMANTIC_KEYWORD_LIMIT = 100
LEGACY_CONFIG_DIR_NAME = ".memora"
CONFIG_DIR_NAME = LEGACY_CONFIG_DIR_NAME
ENV_MEMORA_HOME = "MEMORA_HOME"
ENV_VAULT_PATH = "MEMORA_VAULT"
ENV_SEMANTIC_PROVIDER = "MEMORA_SEMANTIC_PROVIDER"
ENV_SEMANTIC_MODEL = "MEMORA_SEMANTIC_MODEL"
ENV_AGENT_MEMORY_ENABLED = "MEMORA_AGENT_MEMORY_ENABLED"
ENV_AGENT_AUTO_RECALL = "MEMORA_AGENT_AUTO_RECALL"


class ConfigError(ValueError):
    """Raised when Memora configuration cannot be found or loaded."""


class SemanticConfig(BaseModel):
    """Optional semantic search configuration."""

    model_config = ConfigDict(extra="ignore")

    provider: Optional[str] = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
    command: Optional[list[str]] = None

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


class TaskRecallPolicyConfig(BaseModel):
    """Task-class-specific defaults for automatic context building."""

    budget: int = Field(default=1200, ge=1)
    include_related: bool = False
    include_pending: bool = False
    include_profile: bool = True
    types: list[str] = Field(default_factory=list)

    @field_validator("types")
    @classmethod
    def validate_types(cls, value: list[str]) -> list[str]:
        valid_types = {memory_type.value for memory_type in MemoryType}
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            memory_type = MemoryType(str(item)).value if str(item) in valid_types else str(item)
            if memory_type not in valid_types:
                raise ValueError(f"unknown recall policy memory type: {item}")
            if memory_type in seen:
                continue
            seen.add(memory_type)
            cleaned.append(memory_type)
        return cleaned


def _default_recall_policies() -> dict[str, TaskRecallPolicyConfig]:
    return {
        "default": TaskRecallPolicyConfig(),
        "coding": TaskRecallPolicyConfig(
            budget=900,
            types=[
                MemoryType.DECISION.value,
                MemoryType.PREFERENCE.value,
                MemoryType.PROJECT_CONTEXT.value,
                MemoryType.TASK.value,
            ],
        ),
        "planning": TaskRecallPolicyConfig(
            budget=2000,
            include_related=True,
            types=[
                MemoryType.DECISION.value,
                MemoryType.PROJECT_CONTEXT.value,
                MemoryType.CONVERSATION_SUMMARY.value,
            ],
        ),
        "review": TaskRecallPolicyConfig(
            budget=2400,
            include_pending=True,
            include_profile=False,
        ),
    }


class AgentPolicyConfig(BaseModel):
    """User-configurable rules for AI agent memory behavior."""

    model_config = ConfigDict(use_enum_values=True, validate_default=True, extra="ignore")

    aliases: list[str] = Field(default_factory=lambda: ["Remi", "Рэми", "Реми"])
    enabled: bool = True
    auto_recall: bool = True
    min_active_confidence: float = Field(default=0.85, ge=0, le=1)
    min_pending_confidence: float = Field(default=0.55, ge=0, le=1)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for alias in value:
            normalized = str(alias).strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("agent_policy.aliases must include at least one alias")
        return cleaned

    @model_validator(mode="after")
    def validate_thresholds(self) -> AgentPolicyConfig:
        if self.min_active_confidence < self.min_pending_confidence:
            raise ValueError(
                "min_active_confidence must be greater than or equal to min_pending_confidence"
            )
        return self


class IndexFreshnessConfig(BaseModel):
    """Index freshness settings for CLI commands that refresh before reads."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    debounce_seconds: float = Field(default=2.0, ge=0)
    clean: bool = False
    refresh_before_search: bool = True
    refresh_before_recall: bool = True


class ProfileConfig(BaseModel):
    """Bounded in-memory profile context configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    user_budget: int = Field(default=DEFAULT_USER_RECALL_BUDGET, ge=1)
    project_budget: int = Field(default=DEFAULT_PROJECT_RECALL_BUDGET, ge=1)
    inject_by_default: bool = False


class MemoryConfig(BaseModel):
    """Configuration for a managed local Memora home."""

    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True, extra="ignore")

    schema_version: int = SCHEMA_VERSION
    home_path: Path
    vault_path: Path
    default_project: Optional[str] = None
    semantic: SemanticConfig = Field(default_factory=SemanticConfig)
    agent_policy: AgentPolicyConfig = Field(default_factory=AgentPolicyConfig)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        return value

    @property
    def config_path(self) -> Path:
        return self.home_path / CONFIG_FILE_NAME

    @property
    def raw_dir(self) -> str:
        return DEFAULT_RAW_DIR

    @property
    def memories_dir(self) -> str:
        return DEFAULT_MEMORIES_DIR

    @property
    def sources_dir(self) -> str:
        return DEFAULT_SOURCES_DIR

    @property
    def wiki_dir(self) -> str:
        return DEFAULT_WIKI_DIR

    @property
    def state_dir(self) -> str:
        return DEFAULT_STATE_DIR_NAME

    @property
    def index_path(self) -> str:
        return DEFAULT_INDEX_PATH

    @property
    def recall(self) -> RecallConfig:
        return RecallConfig()

    @property
    def recall_policies(self) -> dict[str, TaskRecallPolicyConfig]:
        return _default_recall_policies()

    @property
    def index_freshness(self) -> IndexFreshnessConfig:
        return IndexFreshnessConfig()

    @property
    def profile(self) -> ProfileConfig:
        return ProfileConfig()

    @property
    def memory_root(self) -> Path:
        return self.vault_path / self.memories_dir

    @property
    def raw_root(self) -> Path:
        return self.vault_path / self.raw_dir

    @property
    def wiki_root(self) -> Path:
        return self.vault_path / self.wiki_dir

    @property
    def state_root(self) -> Path:
        return self.home_path / self.state_dir

    @property
    def index_file(self) -> Path:
        return self.home_path / self.index_path


def default_memora_home() -> Path:
    """Return the default managed Memora home."""

    return Path.home().joinpath(DEFAULT_HOME_DIR_NAME).resolve()


def resolve_memora_home(home_path: Optional[Union[Path, str]] = None) -> Path:
    """Resolve the managed Memora home from an explicit path or environment."""

    if home_path is not None:
        return Path(home_path).expanduser().resolve()
    env_home = os.environ.get(ENV_MEMORA_HOME)
    if env_home:
        return Path(env_home).expanduser().resolve()
    return default_memora_home()


def create_default_config(
    vault_path: Optional[Union[Path, str]] = None,
    *,
    home_path: Optional[Union[Path, str]] = None,
) -> MemoryConfig:
    """Create the default config model for a managed Memora home."""

    if vault_path is None:
        resolved_home = resolve_memora_home(home_path)
        resolved_vault = resolved_home / DEFAULT_VAULT_DIR_NAME
    else:
        resolved_vault = Path(vault_path).expanduser().resolve()
        if home_path is not None or os.environ.get(ENV_MEMORA_HOME):
            resolved_home = resolve_memora_home(home_path)
        elif resolved_vault.name == DEFAULT_VAULT_DIR_NAME:
            resolved_home = resolved_vault.parent
        else:
            resolved_home = resolved_vault
    return MemoryConfig(home_path=resolved_home, vault_path=resolved_vault)


def set_agent_aliases(vault_path: Union[Path, str], aliases: Sequence[str]) -> list[str]:
    """Persist `agent_policy.aliases` in managed config and return normalized aliases."""

    normalized = AgentPolicyConfig(aliases=list(aliases)).aliases
    config = load_config(vault_path)
    updated = config.model_copy(
        update={"agent_policy": config.agent_policy.model_copy(update={"aliases": normalized})}
    )
    write_config(updated, overwrite=True)
    return updated.agent_policy.aliases


def write_config(config: MemoryConfig, *, overwrite: bool = False) -> bool:
    """Write the managed `config.yaml`.

    Returns True when a file was written and False when an existing config was
    preserved.
    """

    config_path = config.config_path
    if config_path.exists() and not overwrite:
        return False

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_data = config.model_dump(mode="json", exclude={"home_path", "vault_path"})
    config_path.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
    return True


def load_config(
    vault_path: Optional[Union[Path, str]] = None,
    *,
    start_path: Optional[Union[Path, str]] = None,
    home_path: Optional[Union[Path, str]] = None,
) -> MemoryConfig:
    """Load config from an explicit path, `MEMORA_HOME`, or nearest parent."""

    resolved_home, resolved_vault, config_path = _resolve_layout_paths(
        vault_path,
        start_path=start_path,
        home_path=home_path,
    )
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
        return MemoryConfig.model_validate(
            _apply_environment_overrides(
                {**loaded, "home_path": resolved_home, "vault_path": resolved_vault}
            )
        )
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def find_config_path(start_path: Optional[Union[Path, str]] = None) -> Optional[Path]:
    """Find the nearest parent directory containing managed or legacy config."""

    current = Path(start_path or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        config_path = candidate / CONFIG_FILE_NAME
        if config_path.exists():
            return config_path
        legacy_config_path = candidate / LEGACY_CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if legacy_config_path.exists():
            return legacy_config_path
    return None


def _resolve_layout_paths(
    vault_path: Optional[Union[Path, str]],
    *,
    start_path: Optional[Union[Path, str]] = None,
    home_path: Optional[Union[Path, str]] = None,
) -> tuple[Path, Path, Path]:
    if home_path is not None:
        home = resolve_memora_home(home_path)
        vault = Path(vault_path).expanduser().resolve() if vault_path is not None else home / DEFAULT_VAULT_DIR_NAME
        return home, vault, home / CONFIG_FILE_NAME

    if vault_path is not None:
        vault = Path(vault_path).expanduser().resolve()
        managed_home = vault.parent if vault.name == DEFAULT_VAULT_DIR_NAME else vault
        managed_config_path = managed_home / CONFIG_FILE_NAME
        legacy_config_path = vault / LEGACY_CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if managed_config_path.exists() or not legacy_config_path.exists():
            return managed_home, vault, managed_config_path
        return vault, vault, legacy_config_path

    env_home = os.environ.get(ENV_MEMORA_HOME)
    if env_home:
        home = Path(env_home).expanduser().resolve()
        return home, home / DEFAULT_VAULT_DIR_NAME, home / CONFIG_FILE_NAME

    config_path = find_config_path(start_path=start_path)
    if config_path:
        if config_path.parent.name == LEGACY_CONFIG_DIR_NAME:
            vault = config_path.parent.parent
            return vault, vault, config_path
        home = config_path.parent
        vault = home / DEFAULT_VAULT_DIR_NAME if (home / DEFAULT_VAULT_DIR_NAME).exists() else home
        return home, vault, config_path

    env_vault = os.environ.get(ENV_VAULT_PATH)
    if env_vault:
        vault = Path(env_vault).expanduser().resolve()
        managed_home = vault.parent if vault.name == DEFAULT_VAULT_DIR_NAME else vault
        managed_config_path = managed_home / CONFIG_FILE_NAME
        legacy_config_path = vault / LEGACY_CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if managed_config_path.exists() or not legacy_config_path.exists():
            return managed_home, vault, managed_config_path
        return vault, vault, legacy_config_path

    raise ConfigError(
        f"could not find {CONFIG_FILE_NAME}; set {ENV_MEMORA_HOME} or pass --vault"
    )


def _apply_environment_overrides(config_data: dict[str, Any]) -> dict[str, Any]:
    semantic_overrides: dict[str, Any] = {}
    for env_name, field_name in (
        (ENV_SEMANTIC_PROVIDER, "provider"),
        (ENV_SEMANTIC_MODEL, "model"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            semantic_overrides[field_name] = value

    agent_policy_overrides: dict[str, Any] = {}
    for env_name, field_name in (
        (ENV_AGENT_MEMORY_ENABLED, "enabled"),
        (ENV_AGENT_AUTO_RECALL, "auto_recall"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            agent_policy_overrides[field_name] = value

    if not semantic_overrides and not agent_policy_overrides:
        return config_data

    semantic_config = config_data.get("semantic") or {}
    if not isinstance(semantic_config, dict):
        semantic_config = {}
    agent_policy_config = config_data.get("agent_policy") or {}
    if not isinstance(agent_policy_config, dict):
        agent_policy_config = {}
    return {
        **config_data,
        "semantic": {
            **semantic_config,
            **semantic_overrides,
        },
        "agent_policy": {
            **agent_policy_config,
            **agent_policy_overrides,
        },
    }


