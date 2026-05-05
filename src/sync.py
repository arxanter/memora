"""Sync, conflict detection, atomic write, and local lock helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import yaml
from pydantic import ValidationError

from config import MemoryConfig
from schema import iter_memory_markdown_files, parse_markdown_document

PathLike = Union[Path, str]


class LockTimeout(TimeoutError):
    """Raised when a local vault lock cannot be acquired in time."""


@dataclass(frozen=True)
class ConflictIssue:
    """One practical Markdown sync conflict or warning."""

    kind: str
    path: Path
    message: str
    severity: str = "error"
    memory_id: Optional[str] = None
    line: Optional[int] = None
    paths: tuple[Path, ...] = ()

    def to_dict(self, vault_path: Optional[PathLike] = None) -> dict[str, Any]:
        root = Path(vault_path).resolve() if vault_path else None
        path = _display_path(self.path, root)
        payload: dict[str, Any] = {
            "kind": self.kind,
            "severity": self.severity,
            "path": path,
            "message": self.message,
        }
        if self.memory_id is not None:
            payload["id"] = self.memory_id
        if self.line is not None:
            payload["line"] = self.line
        if self.paths:
            payload["paths"] = [_display_path(item, root) for item in self.paths]
        return payload


@dataclass(frozen=True)
class ConflictReport:
    """Aggregated conflict detection result for syncable Markdown."""

    vault_path: Path
    markdown_files: int
    memory_files: int
    issues: tuple[ConflictIssue, ...]

    @property
    def conflicts(self) -> tuple[ConflictIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ConflictIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity != "error")

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "implemented": True,
            "vault_path": str(self.vault_path),
            "markdown_files": self.markdown_files,
            "memory_files": self.memory_files,
            "conflict_count": len(self.conflicts),
            "warning_count": len(self.warnings),
            "issue_count": len(self.issues),
            "issues": [issue.to_dict(self.vault_path) for issue in self.issues],
        }


class VaultLock:
    """Small local lock based on atomic directory creation.

    This coordinates local processes that agree to use Memora locks. It is
    intentionally not a distributed lock for synced folders.
    """

    def __init__(
        self,
        config: MemoryConfig,
        *,
        name: str = "vault",
        timeout_seconds: float = 10.0,
        poll_seconds: float = 0.05,
    ) -> None:
        self.config = config
        self.name = _safe_lock_name(name)
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.path = config.state_root / "locks" / f"{self.name}.lock"
        self._acquired = False

    def __enter__(self) -> VaultLock:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.path.mkdir(parents=True)
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"timed out waiting for vault lock: {self.path}") from exc
                time.sleep(self.poll_seconds)
                continue

            self._acquired = True
            owner = {
                "pid": os.getpid(),
                "created_at": time.time(),
                "name": self.name,
            }
            (self.path / "owner.json").write_text(
                json.dumps(owner, sort_keys=True), encoding="utf-8"
            )
            return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._acquired:
            return
        for child in self.path.iterdir():
            child.unlink()
        self.path.rmdir()
        self._acquired = False


def vault_lock(
    config: MemoryConfig,
    *,
    name: str = "vault",
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.05,
) -> VaultLock:
    """Return a local vault lock context manager."""

    return VaultLock(
        config,
        name=name,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def atomic_write_text(path: PathLike, content: str) -> None:
    """Atomically replace one text file using a temp file in the same directory."""

    atomic_write_many(((Path(path), content),))


def atomic_write_many(files: Sequence[tuple[PathLike, str]]) -> None:
    """Prepare temp files first, then atomically replace each target path."""

    temp_paths: list[Path] = []
    prepared: list[tuple[Path, Path]] = []
    try:
        for raw_path, content in files:
            path = Path(raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = _write_temp_file(path, content)
            temp_paths.append(temp_path)
            prepared.append((path, temp_path))

        for path, temp_path in prepared:
            os.replace(temp_path, path)
            temp_paths.remove(temp_path)

        for directory in {path.parent for path, _ in prepared}:
            _fsync_directory(directory)
    finally:
        for temp_path in temp_paths:
            if temp_path.exists():
                temp_path.unlink()


def detect_sync_conflicts(config: MemoryConfig) -> ConflictReport:
    """Detect practical Markdown sync conflicts without attempting resolution."""

    markdown_files = _iter_syncable_markdown_files(config.vault_path, config.memora_dir)
    memory_files = iter_memory_markdown_files(config.vault_path)
    issues: list[ConflictIssue] = []
    raw_by_path: dict[Path, str] = {}

    for path in markdown_files:
        try:
            raw = path.read_text(encoding="utf-8")
            raw_by_path[path] = raw
        except OSError as exc:
            issues.append(
                ConflictIssue(
                    kind="read_error",
                    path=path,
                    message=f"could not read Markdown file: {exc}",
                )
            )
            continue

        marker_line = _first_conflict_marker_line(raw)
        if marker_line is not None:
            issues.append(
                ConflictIssue(
                    kind="conflict_marker",
                    path=path,
                    line=marker_line,
                    message="sync conflict markers found; resolve the Markdown file manually",
                )
            )

    paths_by_id: dict[str, list[Path]] = {}
    for path in memory_files:
        raw = raw_by_path.get(path)
        if raw is None:
            continue
        try:
            document = parse_markdown_document(raw, path=path)
        except (ValueError, ValidationError, yaml.YAMLError) as exc:
            issues.append(
                ConflictIssue(
                    kind="invalid_frontmatter",
                    path=path,
                    message=f"memory frontmatter is invalid: {exc}",
                )
            )
            continue
        paths_by_id.setdefault(document.frontmatter.id, []).append(path)

    for memory_id, paths in sorted(paths_by_id.items()):
        if len(paths) < 2:
            continue
        issues.append(
            ConflictIssue(
                kind="duplicate_id",
                path=paths[0],
                memory_id=memory_id,
                paths=tuple(paths),
                message=f"duplicate memory id {memory_id!r} appears in {len(paths)} files",
            )
        )

    return ConflictReport(
        vault_path=config.vault_path,
        markdown_files=len(markdown_files),
        memory_files=len(memory_files),
        issues=tuple(issues),
    )


def _write_temp_file(path: Path, content: str) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent), text=True)
    temp_path = Path(temp_name)
    try:
        if path.exists():
            os.chmod(temp_path, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if fd != -1:
            os.close(fd)
        if temp_path.exists():
            temp_path.unlink()
        raise
    return temp_path


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _iter_syncable_markdown_files(vault_path: PathLike, memora_dir: str) -> tuple[Path, ...]:
    root = Path(vault_path)
    local_state_root = root / memora_dir
    return tuple(
        sorted(
            path
            for path in root.rglob("*.md")
            if path.is_file() and not _is_relative_to(path, local_state_root)
        )
    )


def _first_conflict_marker_line(markdown: str) -> Optional[int]:
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if (
            stripped.startswith("<<<<<<<")
            or stripped.startswith(">>>>>>>")
            or stripped == "======="
        ):
            return line_number
    return None


def _safe_lock_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    return cleaned or "vault"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _display_path(path: Path, root: Optional[Path]) -> str:
    if root is not None:
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            pass
    return str(path)


__all__ = [
    "ConflictIssue",
    "ConflictReport",
    "LockTimeout",
    "VaultLock",
    "atomic_write_many",
    "atomic_write_text",
    "detect_sync_conflicts",
    "vault_lock",
]
