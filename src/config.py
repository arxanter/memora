"""Configuration loading for Memora vaults."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from schema import LifecycleStatus, MemoryScope, MemoryType, SCHEMA_VERSION

CONFIG_DIR_NAME = ".memora"
CONFIG_FILE_NAME = "config.yaml"
ENV_VAULT_PATH = "MEMORA_VAULT"
ENV_SEMANTIC_PROVIDER = "MEMORA_SEMANTIC_PROVIDER"
ENV_SEMANTIC_MODEL = "MEMORA_SEMANTIC_MODEL"
ENV_SEMANTIC_BATCH_SIZE = "MEMORA_SEMANTIC_BATCH_SIZE"
ENV_SEMANTIC_DIMENSIONS = "MEMORA_SEMANTIC_DIMENSIONS"
ENV_SEMANTIC_MIN_SIMILARITY = "MEMORA_SEMANTIC_MIN_SIMILARITY"
ENV_FRESHNESS_ENABLED = "MEMORA_FRESHNESS_ENABLED"
ENV_FRESHNESS_INTERVAL_SECONDS = "MEMORA_FRESHNESS_INTERVAL_SECONDS"
ENV_FRESHNESS_DEBOUNCE_SECONDS = "MEMORA_FRESHNESS_DEBOUNCE_SECONDS"
ENV_FRESHNESS_CLEAN = "MEMORA_FRESHNESS_CLEAN"
ENV_FRESHNESS_REFRESH_BEFORE_SEARCH = "MEMORA_FRESHNESS_REFRESH_BEFORE_SEARCH"
ENV_FRESHNESS_REFRESH_BEFORE_RECALL = "MEMORA_FRESHNESS_REFRESH_BEFORE_RECALL"
ENV_AGENT_TRUST_LEVEL = "MEMORA_TRUST_LEVEL"
ENV_AGENT_DEFAULT_RECALL_BUDGET = "MEMORA_DEFAULT_RECALL_BUDGET"
ENV_AGENT_MEMORY_ENABLED = "MEMORA_AGENT_MEMORY_ENABLED"
ENV_AGENT_AUTO_RECALL = "MEMORA_AGENT_AUTO_RECALL"
ENV_AGENT_SESSION_CAPTURE = "MEMORA_AGENT_SESSION_CAPTURE"
ENV_PROFILE_ENABLED = "MEMORA_PROFILE_ENABLED"
ENV_PROFILE_USER_BUDGET = "MEMORA_PROFILE_USER_BUDGET"
ENV_PROFILE_PROJECT_BUDGET = "MEMORA_PROFILE_PROJECT_BUDGET"
ENV_PROFILE_INJECT_BY_DEFAULT = "MEMORA_PROFILE_INJECT_BY_DEFAULT"


class AgentTrustLevel(str, Enum):
    """How much autonomy agents have when writing or mutating memory."""

    MANUAL = "manual"
    REVIEW = "review"
    EXPLICIT_ACTIVE = "explicit_active"
    AUTONOMOUS = "autonomous"


class ConfigError(ValueError):
    """Raised when a vault config cannot be found or loaded."""


class SemanticConfig(BaseModel):
    """Optional semantic search configuration."""

    provider: Optional[str] = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
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
                MemoryType.SOURCE_EXTRACT.value,
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

    model_config = ConfigDict(use_enum_values=True, validate_default=True)

    aliases: list[str] = Field(default_factory=lambda: ["Remi", "Рэми", "Реми"])
    enabled: bool = True
    auto_recall: bool = True
    session_capture: bool = True
    trust_level: AgentTrustLevel = AgentTrustLevel.REVIEW
    default_recall_budget: int = Field(default=1200, ge=1)
    min_active_confidence: float = Field(default=0.85, ge=0, le=1)
    min_pending_confidence: float = Field(default=0.55, ge=0, le=1)
    explicit_user_saves_active: bool = True
    autonomous_lifecycle: bool = False
    require_review_for_source_extracts: bool = True

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

    enabled: bool = True
    interval_seconds: int = Field(default=30, ge=1)
    debounce_seconds: float = Field(default=2.0, ge=0)
    clean: bool = False
    refresh_before_search: bool = True
    refresh_before_recall: bool = True


class ProfileConfig(BaseModel):
    """Bounded in-memory profile context configuration."""

    enabled: bool = True
    user_budget: int = Field(default=500, ge=1)
    project_budget: int = Field(default=700, ge=1)
    inject_by_default: bool = False


class MemoryConfig(BaseModel):
    """Configuration for a local Memora vault."""

    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True)

    schema_version: int = SCHEMA_VERSION
    vault_path: Path
    raw_dir: str = "raw"
    memories_dir: str = "Memories"
    sources_dir: str = "Sources"
    wiki_dir: str = "Wiki"
    memora_dir: str = CONFIG_DIR_NAME
    index_path: str = ".memora/index.sqlite"
    default_scope: MemoryScope = MemoryScope.USER
    default_project: Optional[str] = None
    user_default_status: LifecycleStatus = LifecycleStatus.ACTIVE
    agent_default_status: LifecycleStatus = LifecycleStatus.PENDING
    default_author_name: str = "Memora CLI"
    semantic: SemanticConfig = Field(default_factory=SemanticConfig)
    recall: RecallConfig = Field(default_factory=RecallConfig)
    recall_policies: dict[str, TaskRecallPolicyConfig] = Field(
        default_factory=_default_recall_policies
    )
    agent_policy: AgentPolicyConfig = Field(default_factory=AgentPolicyConfig)
    index_freshness: IndexFreshnessConfig = Field(default_factory=IndexFreshnessConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)

    @model_validator(mode="before")
    @classmethod
    def normalize_recall_policy_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        policies = data.get("recall_policies")
        if not isinstance(policies, dict):
            return data
        review_policy = policies.get("review")
        if isinstance(review_policy, dict) and "include_profile" not in review_policy:
            policies = {**policies, "review": {**review_policy, "include_profile": False}}
            return {**data, "recall_policies": policies}
        return data

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        return value

    @field_validator(
        "raw_dir",
        "memories_dir",
        "sources_dir",
        "wiki_dir",
        "memora_dir",
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
        return self.vault_path / self.memora_dir / CONFIG_FILE_NAME

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
    def index_file(self) -> Path:
        return self.vault_path / self.index_path


def create_default_config(vault_path: Union[Path, str]) -> MemoryConfig:
    """Create the default config model for a vault path."""

    return MemoryConfig(vault_path=Path(vault_path).expanduser().resolve())


def set_agent_aliases(vault_path: Union[Path, str], aliases: Sequence[str]) -> list[str]:
    """Persist `agent_policy.aliases` in `.memora/config.yaml` and return normalized aliases."""

    normalized = AgentPolicyConfig(aliases=list(aliases)).aliases
    config = load_config(vault_path)
    updated = config.model_copy(
        update={"agent_policy": config.agent_policy.model_copy(update={"aliases": normalized})}
    )
    write_config(updated, overwrite=True)
    return updated.agent_policy.aliases


def write_config(config: MemoryConfig, *, overwrite: bool = False) -> bool:
    """Write `.memora/config.yaml`.

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
        return MemoryConfig.model_validate(
            _apply_environment_overrides({**loaded, "vault_path": resolved_vault})
        )
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def find_config_path(start_path: Optional[Union[Path, str]] = None) -> Optional[Path]:
    """Find the nearest parent directory containing `.memora/config.yaml`."""

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
        (ENV_FRESHNESS_REFRESH_BEFORE_SEARCH, "refresh_before_search"),
        (ENV_FRESHNESS_REFRESH_BEFORE_RECALL, "refresh_before_recall"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            freshness_overrides[field_name] = value

    agent_policy_overrides: dict[str, Any] = {}
    for env_name, field_name in (
        (ENV_AGENT_TRUST_LEVEL, "trust_level"),
        (ENV_AGENT_DEFAULT_RECALL_BUDGET, "default_recall_budget"),
        (ENV_AGENT_MEMORY_ENABLED, "enabled"),
        (ENV_AGENT_AUTO_RECALL, "auto_recall"),
        (ENV_AGENT_SESSION_CAPTURE, "session_capture"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            agent_policy_overrides[field_name] = value

    profile_overrides: dict[str, Any] = {}
    for env_name, field_name in (
        (ENV_PROFILE_ENABLED, "enabled"),
        (ENV_PROFILE_USER_BUDGET, "user_budget"),
        (ENV_PROFILE_PROJECT_BUDGET, "project_budget"),
        (ENV_PROFILE_INJECT_BY_DEFAULT, "inject_by_default"),
    ):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            profile_overrides[field_name] = value

    if (
        not semantic_overrides
        and not freshness_overrides
        and not agent_policy_overrides
        and not profile_overrides
    ):
        return config_data

    semantic_config = config_data.get("semantic") or {}
    if not isinstance(semantic_config, dict):
        semantic_config = {}
    freshness_config = config_data.get("index_freshness") or {}
    if not isinstance(freshness_config, dict):
        freshness_config = {}
    agent_policy_config = config_data.get("agent_policy") or {}
    if not isinstance(agent_policy_config, dict):
        agent_policy_config = {}
    profile_config = config_data.get("profile") or {}
    if not isinstance(profile_config, dict):
        profile_config = {}
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
        "agent_policy": {
            **agent_policy_config,
            **agent_policy_overrides,
        },
        "profile": {
            **profile_config,
            **profile_overrides,
        },
    }


def config_to_dict(config: MemoryConfig) -> dict[str, Any]:
    """Return a JSON-safe config summary."""

    data = config.model_dump(mode="json")
    data["vault_path"] = str(config.vault_path)
    data["config_path"] = str(config.config_path)
    return data
