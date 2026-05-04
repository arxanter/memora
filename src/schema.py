"""Pydantic models and validators for Memora vault Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_VERSION = 1

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(?P<yaml>.*?)(?:\n---[ \t]*)(?:\n(?P<body>.*))?\Z", re.DOTALL)
_MEMORY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")


class MemoryType(str, Enum):
    """Canonical memory categories for Stage 1."""

    FACT = "fact"
    PREFERENCE = "preference"
    DECISION = "decision"
    TASK = "task"
    SOURCE_EXTRACT = "source_extract"
    PROJECT_CONTEXT = "project_context"
    CONVERSATION_SUMMARY = "conversation_summary"


class LifecycleStatus(str, Enum):
    """Durable lifecycle states stored in Markdown frontmatter."""

    PENDING = "pending"
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class MemoryScope(str, Enum):
    """Recall boundary for a memory."""

    USER = "user"
    PROJECT = "project"
    GLOBAL = "global"


class AuthorKind(str, Enum):
    """Author/source kind used for conditional schema rules."""

    USER = "user"
    AGENT = "agent"
    IMPORT = "import"


class RelationType(str, Enum):
    """Directional relation vocabulary for the durable graph."""

    SUPPORTS = "supports"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    RELATED_TO = "related_to"
    BELONGS_TO_PROJECT = "belongs_to_project"


class SourceRef(BaseModel):
    """Pointer to source material that supports or produced a memory."""

    model_config = ConfigDict(extra="allow")

    path: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None

    @model_validator(mode="after")
    def require_reference(self) -> SourceRef:
        if not self.path and not self.url:
            raise ValueError("source must include path or url")
        return self


class AuthorMetadata(BaseModel):
    """Metadata about who or what created the memory."""

    model_config = ConfigDict(extra="allow")

    kind: AuthorKind = AuthorKind.USER
    name: Optional[str] = None


class MigrationInfo(BaseModel):
    """Durable migration marker for frontmatter rewritten between schema versions."""

    model_config = ConfigDict(extra="allow")

    from_schema_version: int = Field(ge=0)
    migrated_at: datetime
    tool: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("migrated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("migrated_at must include a timezone")
        return value


class Observation(BaseModel):
    """Atomic recall unit embedded in a memory."""

    model_config = ConfigDict(extra="allow")

    category: str = Field(min_length=1)
    text: str = Field(min_length=1)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class Relation(BaseModel):
    """Directional graph edge to another memory id."""

    model_config = ConfigDict(extra="allow")

    type: RelationType
    target: str = Field(min_length=1)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @field_validator("target")
    @classmethod
    def validate_target_id(cls, value: str) -> str:
        if not _MEMORY_ID_RE.match(value):
            raise ValueError("relation target must be a memory id without whitespace")
        return value


class MemoryFrontmatter(BaseModel):
    """Stage 1 frontmatter schema for a canonical memory Markdown file."""

    model_config = ConfigDict(extra="allow")

    schema_version: int
    id: str = Field(min_length=1)
    title: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    type: MemoryType
    scope: MemoryScope = MemoryScope.USER
    project: Optional[str] = None
    status: LifecycleStatus
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    created_at: datetime
    updated_at: datetime
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: Optional[SourceRef] = None
    author: Optional[AuthorMetadata] = None
    migration: Optional[MigrationInfo] = None
    supersedes: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    relation_links: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        return value

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _MEMORY_ID_RE.match(value):
            raise ValueError("id must start with a letter and contain no whitespace")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("supersedes", "contradicts")
    @classmethod
    def validate_link_ids(cls, values: list[str]) -> list[str]:
        invalid = [value for value in values if not _MEMORY_ID_RE.match(value)]
        if invalid:
            raise ValueError(f"link ids must be memory ids without whitespace: {invalid}")
        return values

    @field_validator("aliases", "source_links", "relation_links", mode="before")
    @classmethod
    def normalize_presentation_links(cls, values: Any) -> list[str]:
        if values in (None, ""):
            return []
        if isinstance(values, str):
            values = [values]
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = str(value).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        return cleaned

    @field_validator("risk_flags", mode="before")
    @classmethod
    def normalize_risk_flags(cls, values: Any) -> list[str]:
        if values in (None, ""):
            return []
        if isinstance(values, str):
            values = [values]
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            flag = str(value).strip().lower()
            if not flag or flag in seen:
                continue
            seen.add(flag)
            cleaned.append(flag)
        return cleaned

    @model_validator(mode="after")
    def validate_conditionals(self) -> MemoryFrontmatter:
        if self.scope == MemoryScope.PROJECT and not self.project:
            raise ValueError("project-scoped memory must include project")

        if self.author and self.author.kind == AuthorKind.AGENT:
            if self.source is None:
                raise ValueError("agent-generated memory must include source")
            if self.confidence is None:
                raise ValueError("agent-generated memory must include confidence")

        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be greater than or equal to created_at")

        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from")

        if self.migration and self.migration.from_schema_version >= self.schema_version:
            raise ValueError("migration.from_schema_version must be less than schema_version")

        return self


class MemoryDocument(BaseModel):
    """A parsed Markdown memory document."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    frontmatter: MemoryFrontmatter
    body: str
    path: Optional[Path] = None


@dataclass(frozen=True)
class ValidationIssue:
    """Validation failure for one Markdown file."""

    path: Path
    message: str


@dataclass(frozen=True)
class VaultValidationReport:
    """Aggregated validation result for a vault's canonical memories."""

    documents: tuple[MemoryDocument, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def parse_markdown_document(markdown: str, path: Optional[Union[Path, str]] = None) -> MemoryDocument:
    """Parse one Obsidian-compatible Markdown memory and validate frontmatter."""

    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        raise ValueError("memory Markdown must start with YAML frontmatter delimited by ---")

    loaded = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter must be a YAML mapping")

    frontmatter = MemoryFrontmatter.model_validate(_normalize_yaml_values(loaded))
    return MemoryDocument(frontmatter=frontmatter, body=match.group("body") or "", path=Path(path) if path else None)


def validate_markdown_file(path: Union[Path, str]) -> MemoryDocument:
    """Read and validate a single memory Markdown file."""

    file_path = Path(path)
    return parse_markdown_document(file_path.read_text(encoding="utf-8"), path=file_path)


def iter_memory_markdown_files(vault_path: Union[Path, str]) -> tuple[Path, ...]:
    """Return canonical memory Markdown files under a vault's Memories directory."""

    memories_path = Path(vault_path) / "Memories"
    if not memories_path.exists():
        return ()
    return tuple(sorted(path for path in memories_path.rglob("*.md") if path.is_file()))


def validate_vault(vault_path: Union[Path, str]) -> VaultValidationReport:
    """Validate every canonical memory Markdown file in a vault."""

    root = Path(vault_path)
    memories_path = root / "Memories"
    if not memories_path.exists():
        return VaultValidationReport(
            documents=(),
            issues=(ValidationIssue(path=memories_path, message="vault is missing Memories directory"),),
        )

    documents: list[MemoryDocument] = []
    issues: list[ValidationIssue] = []
    for path in iter_memory_markdown_files(root):
        try:
            documents.append(validate_markdown_file(path))
        except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
            issues.append(ValidationIssue(path=path, message=str(exc)))

    return VaultValidationReport(documents=tuple(documents), issues=tuple(issues))


def _normalize_yaml_values(value: Any) -> Any:
    """Normalize YAML scalar surprises before handing data to Pydantic."""

    if isinstance(value, dict):
        return {key: _normalize_yaml_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_values(item) for item in value]
    return value
