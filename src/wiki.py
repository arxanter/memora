"""Karpathy-style LLM Wiki helpers for Memora vaults."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import yaml

from config import MemoryConfig
from indexer import estimate_tokens
from markdown import readable_title, wikilink_for_path
from sync import atomic_write_text, vault_lock

WIKI_PAGE_TYPES = {"source", "entity", "concept", "synthesis", "overview", "index", "log"}
WIKI_SEARCH_EXTENSIONS = {".md", ".markdown"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL)
QUERY_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "as",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "with",
}


@dataclass(frozen=True)
class WikiPage:
    path: Path
    relative_path: Path
    page_type: str
    title: str
    frontmatter: Mapping[str, Any]
    body: str

    @property
    def citation(self) -> dict[str, str]:
        return {
            "id": self.relative_path.as_posix(),
            "path": self.relative_path.as_posix(),
            "kind": "wiki",
        }

    def preview(self, *, max_chars: int = 600) -> str:
        text = _plain_text(self.body)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "..."

    def to_dict(self, *, full: bool = False, max_chars: int = 600) -> dict[str, Any]:
        body = self.body.strip() if full else self.preview(max_chars=max_chars)
        return {
            "path": str(self.path),
            "relative_path": self.relative_path.as_posix(),
            "type": self.page_type,
            "title": self.title,
            "frontmatter": dict(self.frontmatter),
            "body": body,
            "tokens_estimate": estimate_tokens(body),
            "citation": self.citation,
        }


@dataclass(frozen=True)
class WikiSearchResult:
    page: WikiPage
    score: float
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.page.relative_path.as_posix(),
            "title": self.page.title,
            "type": self.page.page_type,
            "score": round(self.score, 6),
            "snippet": self.snippet,
            "citation": self.page.citation,
        }


def wiki_status(config: MemoryConfig) -> dict[str, Any]:
    pages = iter_wiki_pages(config)
    type_counts: dict[str, int] = {}
    for page in pages:
        type_counts[page.page_type] = type_counts.get(page.page_type, 0) + 1
    lint = wiki_lint(config)
    log_entries = _recent_log_entries(config, limit=5)
    return {
        "ok": not lint["issues"],
        "implemented": True,
        "command": "wiki status",
        "wiki_path": str(config.wiki_root),
        "page_count": len(pages),
        "type_counts": type_counts,
        "issue_count": len(lint["issues"]),
        "recent_log": log_entries,
    }


def wiki_read(
    config: MemoryConfig,
    target: str,
    *,
    full: bool = False,
    max_chars: int = 1200,
) -> dict[str, Any]:
    page = resolve_wiki_page(config, target)
    return {
        "ok": True,
        "implemented": True,
        "command": "wiki read",
        "page": page.to_dict(full=full, max_chars=max_chars),
    }


def wiki_search(config: MemoryConfig, query: str, *, limit: int = 10) -> dict[str, Any]:
    results = search_wiki_pages(config, query, limit=limit)
    return {
        "ok": True,
        "implemented": True,
        "command": "wiki search",
        "query": query,
        "result_count": len(results),
        "results": [result.to_dict() for result in results],
    }


def search_wiki_pages(
    config: MemoryConfig,
    query: str,
    *,
    limit: int = 10,
    page_types: Optional[Iterable[str]] = None,
) -> tuple[WikiSearchResult, ...]:
    tokens = _query_tokens(query)
    allowed_types = {str(item) for item in page_types or ()}
    results: list[WikiSearchResult] = []
    for page in iter_wiki_pages(config):
        if allowed_types and page.page_type not in allowed_types:
            continue
        haystack = f"{page.title}\n{page.body}".casefold()
        score = _score_text(tokens, haystack, title=page.title)
        if score <= 0 and tokens:
            continue
        snippet = _snippet(page.body, tokens)
        results.append(WikiSearchResult(page=page, score=score, snippet=snippet))
    results.sort(key=lambda item: (-item.score, item.page.relative_path.as_posix()))
    return tuple(results[:limit])


def wiki_ingest_source(
    config: MemoryConfig,
    source_id: str,
    *,
    title: Optional[str] = None,
    entities: Sequence[str] = (),
    concepts: Sequence[str] = (),
) -> dict[str, Any]:
    source_dir = config.vault_path / config.sources_dir / source_id
    if not source_dir.exists():
        raise ValueError(f"source not found: {source_id}")

    source_path = source_dir / "source.md"
    extract_path = source_dir / "extract.md"
    selected_path = extract_path if extract_path.exists() else source_path
    if not selected_path.exists():
        raise ValueError(f"source has no source.md or extract.md: {source_id}")

    source_meta, source_body = _read_markdown(selected_path)
    selected_title = title or str(source_meta.get("title") or source_id)
    slug = _slugify(source_id)
    now = _now()
    relative_source = selected_path.relative_to(config.vault_path).as_posix()
    wiki_path = config.wiki_root / "sources" / f"{slug}.md"
    body = "\n".join(
        [
            f"# {selected_title}",
            "",
            "## Summary",
            "",
            _first_paragraph(source_body) or "No extract summary was available.",
            "",
            "## Source Evidence",
            "",
            f"- Evidence: {wikilink_for_path(relative_source, label=selected_title)}",
            "",
            "## Connections",
            "",
            *[f"- [[{_page_name(entity)}]]" for entity in entities],
            *[f"- [[{_page_name(concept)}]]" for concept in concepts],
        ]
    )
    frontmatter = {
        "title": selected_title,
        "type": "source",
        "source_id": source_id,
        "sources": [relative_source],
        "entities": list(entities),
        "concepts": list(concepts),
        "last_updated": now,
    }
    written = _write_wiki_page(config, wiki_path, frontmatter, body)
    touched = [written.relative_to(config.vault_path).as_posix()]

    for entity in entities:
        touched.append(
            _ensure_named_page(
                config,
                "entities",
                entity,
                page_type="entity",
                source_ref=written.relative_to(config.vault_path).as_posix(),
            ).relative_to(config.vault_path).as_posix()
        )
    for concept in concepts:
        touched.append(
            _ensure_named_page(
                config,
                "concepts",
                concept,
                page_type="concept",
                source_ref=written.relative_to(config.vault_path).as_posix(),
            ).relative_to(config.vault_path).as_posix()
        )

    _append_log(config, f"ingest | {selected_title} | {written.relative_to(config.vault_path)}")
    _update_index(config)
    return {
        "ok": True,
        "implemented": True,
        "command": "wiki ingest",
        "source_id": source_id,
        "wiki_page": written.relative_to(config.vault_path).as_posix(),
        "touched_pages": touched,
    }


def wiki_synthesize(
    config: MemoryConfig,
    question: str,
    *,
    title: Optional[str] = None,
    save: bool = False,
    wiki_results: Sequence[Mapping[str, Any]] = (),
    memory_results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    selected_title = title or readable_title(question, fallback="Synthesis", max_words=8)
    now = _now()
    body_lines = [
        f"# {selected_title}",
        "",
        f"Question: {question}",
        "",
        "## Draft Synthesis",
        "",
        "This synthesis was generated as a durable shell for an agent-authored answer.",
        "Review and complete it before treating it as canonical wiki material.",
        "",
        "## Supporting Wiki Pages",
    ]
    body_lines.extend(_citation_lines(wiki_results) or ["- None selected."])
    body_lines.extend(["", "## Supporting Memories"])
    body_lines.extend(_citation_lines(memory_results) or ["- None selected."])
    frontmatter = {
        "title": selected_title,
        "type": "synthesis",
        "question": question,
        "created_at": now,
        "last_updated": now,
        "sources": [str(item.get("path") or "") for item in wiki_results if item.get("path")],
        "memories": [str(item.get("id") or "") for item in memory_results if item.get("id")],
    }
    slug = _slugify(selected_title)
    target = config.wiki_root / "syntheses" / f"{slug}.md"
    markdown = _render_page(frontmatter, "\n".join(body_lines))
    payload = {
        "ok": True,
        "implemented": True,
        "command": "wiki synthesize",
        "saved": False,
        "relative_path": target.relative_to(config.vault_path).as_posix(),
        "title": selected_title,
        "markdown": markdown,
        "tokens_estimate": estimate_tokens(markdown),
    }
    if save:
        with vault_lock(config):
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, markdown)
            _append_log_unlocked(config, f"synthesis | {selected_title} | {target.relative_to(config.vault_path)}")
            _update_index_unlocked(config)
        payload["saved"] = True
    return payload


def wiki_lint(config: MemoryConfig) -> dict[str, Any]:
    pages = iter_wiki_pages(config)
    path_targets = {_wikilink_target(page.relative_path) for page in pages}
    inbound: dict[str, int] = {page.relative_path.as_posix(): 0 for page in pages}
    issues: list[dict[str, str]] = []
    for page in pages:
        for target in _wikilinks(page.body):
            if _is_external_vault_link(target):
                continue
            resolved = _resolve_link_target(target, path_targets)
            if resolved is None:
                issues.append(
                    {
                        "kind": "broken_link",
                        "path": page.relative_path.as_posix(),
                        "target": target,
                        "message": f"broken wiki link: {target}",
                    }
                )
                continue
            inbound[resolved] = inbound.get(resolved, 0) + 1
        if page.page_type in {"source", "entity", "concept", "synthesis"}:
            sources = page.frontmatter.get("sources") or page.frontmatter.get("memories")
            if not sources:
                issues.append(
                    {
                        "kind": "missing_citation",
                        "path": page.relative_path.as_posix(),
                        "message": "wiki page has no sources or memories frontmatter",
                    }
                )
    for page in pages:
        rel = page.relative_path.as_posix()
        if page.page_type not in {"index", "log", "overview"} and inbound.get(rel, 0) == 0:
            issues.append(
                {
                    "kind": "orphan_page",
                    "path": rel,
                    "message": "wiki page has no inbound wiki links",
                }
            )
    return {
        "ok": not issues,
        "implemented": True,
        "command": "wiki lint",
        "page_count": len(pages),
        "issue_count": len(issues),
        "issues": issues,
    }


def iter_wiki_pages(config: MemoryConfig) -> tuple[WikiPage, ...]:
    if not config.wiki_root.exists():
        return ()
    pages: list[WikiPage] = []
    for path in sorted(config.wiki_root.rglob("*.md")):
        if not path.is_file() or path.suffix.lower() not in WIKI_SEARCH_EXTENSIONS:
            continue
        try:
            pages.append(_page_from_path(config, path))
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return tuple(pages)


def resolve_wiki_page(config: MemoryConfig, target: str) -> WikiPage:
    selected = _safe_wiki_path(config, target)
    if selected.exists():
        return _page_from_path(config, selected)
    normalized = _slugify(target)
    for page in iter_wiki_pages(config):
        if page.relative_path.as_posix() == target or page.path.stem == normalized:
            return page
        if page.title.casefold() == target.casefold():
            return page
    raise ValueError(f"wiki page not found: {target}")


def search_source_evidence(config: MemoryConfig, query: str, *, limit: int = 5) -> tuple[dict[str, Any], ...]:
    tokens = _query_tokens(query)
    sources_root = config.vault_path / config.sources_dir
    if not sources_root.exists():
        return ()
    results: list[dict[str, Any]] = []
    for path in sorted(sources_root.rglob("*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        score = _score_text(tokens, text.casefold(), title=path.stem)
        if score <= 0 and tokens:
            continue
        relative = path.relative_to(config.vault_path).as_posix()
        source_id = path.parent.name
        results.append(
            {
                "id": source_id,
                "path": relative,
                "kind": "source",
                "score": round(score, 6),
                "snippet": _snippet(text, tokens),
                "citation": {"id": source_id, "path": relative, "kind": "source"},
            }
        )
    results.sort(key=lambda item: (-float(item["score"]), str(item["path"])))
    return tuple(results[:limit])


def _page_from_path(config: MemoryConfig, path: Path) -> WikiPage:
    frontmatter, body = _read_markdown(path)
    page_type = str(frontmatter.get("type") or _default_page_type(path))
    if page_type not in WIKI_PAGE_TYPES:
        page_type = "concept"
    title = str(frontmatter.get("title") or readable_title(body, fallback=path.stem))
    return WikiPage(
        path=path,
        relative_path=path.relative_to(config.vault_path),
        page_type=page_type,
        title=title,
        frontmatter=frontmatter,
        body=body,
    )


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    loaded = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    return loaded, match.group("body")


def _write_wiki_page(
    config: MemoryConfig,
    path: Path | str,
    frontmatter: Mapping[str, Any],
    body: str,
) -> Path:
    if isinstance(path, Path) and path.is_absolute():
        path = path.relative_to(config.wiki_root).as_posix()
    path = _safe_wiki_path(config, str(path))
    markdown = _render_page(frontmatter, body)
    with vault_lock(config):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, markdown)
    return path


def _ensure_named_page(
    config: MemoryConfig,
    folder: str,
    name: str,
    *,
    page_type: str,
    source_ref: str,
) -> Path:
    title = _page_name(name)
    path = config.wiki_root / folder / f"{_slugify(title)}.md"
    if path.exists():
        return path
    body = "\n".join(
        [
            f"# {title}",
            "",
            "## Summary",
            "",
            f"Created from {wikilink_for_path(source_ref)}.",
            "",
            "## Related Sources",
            "",
            f"- {wikilink_for_path(source_ref)}",
        ]
    )
    frontmatter = {
        "title": title,
        "type": page_type,
        "sources": [source_ref],
        "last_updated": _now(),
    }
    return _write_wiki_page(config, path.relative_to(config.wiki_root).as_posix(), frontmatter, body)


def _update_index(config: MemoryConfig) -> None:
    with vault_lock(config):
        _update_index_unlocked(config)


def _update_index_unlocked(config: MemoryConfig) -> None:
    pages = [page for page in iter_wiki_pages(config) if page.page_type != "index"]
    grouped: dict[str, list[WikiPage]] = {}
    for page in pages:
        grouped.setdefault(page.page_type, []).append(page)
    lines = [
        "# Wiki Index",
        "",
        "## Overview",
        "",
        "- [Overview](overview.md) - living synthesis across wiki pages.",
    ]
    for page_type in ("source", "entity", "concept", "synthesis", "log"):
        label = page_type.replace("_", " ").title() + "s"
        lines.extend(["", f"## {label}"])
        for page in sorted(grouped.get(page_type, ()), key=lambda item: item.title.casefold()):
            path = page.relative_path.relative_to(Path(config.wiki_dir)).as_posix()
            lines.append(f"- [{page.title}]({path}) - {page.preview(max_chars=120)}")
    atomic_write_text(
        config.wiki_root / "index.md",
        _render_page({"title": "Wiki Index", "type": "index"}, "\n".join(lines)),
    )


def _append_log(config: MemoryConfig, entry: str) -> None:
    with vault_lock(config):
        _append_log_unlocked(config, entry)


def _append_log_unlocked(config: MemoryConfig, entry: str) -> None:
    path = config.wiki_root / "log.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").rstrip()
    else:
        content = _render_page({"title": "Wiki Log", "type": "log"}, "# Wiki Log\n")
    stamp = datetime.now(timezone.utc).astimezone().date().isoformat()
    atomic_write_text(path, f"{content}\n\n## [{stamp}] {entry}\n")


def _render_page(frontmatter: Mapping[str, Any], body: str) -> str:
    rendered = yaml.safe_dump(dict(frontmatter), sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered}\n---\n\n{body.strip()}\n"


def _safe_wiki_path(config: MemoryConfig, target: str) -> Path:
    path = Path(str(target).strip())
    if path.is_absolute():
        try:
            path = path.relative_to(config.wiki_root)
        except ValueError as exc:
            raise ValueError("wiki path must stay inside Wiki/") from exc
    if path.parts and path.parts[0] == config.wiki_dir:
        path = Path(*path.parts[1:])
    if path.suffix == "":
        path = path.with_suffix(".md")
    candidate = (config.wiki_root / path).resolve()
    try:
        candidate.relative_to(config.wiki_root.resolve())
    except ValueError as exc:
        raise ValueError("wiki path must stay inside Wiki/") from exc
    return candidate


def _default_page_type(path: Path) -> str:
    parent = path.parent.name
    if parent == "sources":
        return "source"
    if parent == "entities":
        return "entity"
    if parent == "concepts":
        return "concept"
    if parent == "syntheses":
        return "synthesis"
    if path.stem in {"index", "log", "overview"}:
        return path.stem
    return "concept"


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_А-Яа-яЁё-]{2,}", query.casefold())
        if token not in QUERY_STOPWORDS
    }


def _score_text(tokens: set[str], text: str, *, title: str) -> float:
    if not tokens:
        return 1.0
    score = 0.0
    title_text = title.casefold()
    for token in tokens:
        if token in title_text:
            score += 4.0
        count = text.count(token)
        if count:
            score += min(3.0, float(count))
    return score


def _snippet(text: str, tokens: set[str], *, max_chars: int = 220) -> str:
    plain = _plain_text(text)
    if not plain:
        return ""
    lowered = plain.casefold()
    positions = [lowered.find(token) for token in tokens if lowered.find(token) >= 0]
    start = max(0, min(positions) - 60) if positions else 0
    snippet = plain[start : start + max_chars].strip()
    if start > 0:
        snippet = "..." + snippet
    if start + max_chars < len(plain):
        snippet = snippet.rstrip() + "..."
    return snippet


def _plain_text(text: str) -> str:
    cleaned = re.sub(r"^---.*?---", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?]]", lambda m: m.group(2) or m.group(1), cleaned)
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug[:80].strip("-") or "untitled"


def _page_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or "Untitled"


def _first_paragraph(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        cleaned = _plain_text(paragraph)
        if cleaned:
            return cleaned
    return ""


def _citation_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        path = str(item.get("path") or item.get("relative_path") or "")
        title = str(item.get("title") or item.get("id") or path)
        if not path:
            continue
        lines.append(f"- {wikilink_for_path(path, label=title)}")
    return lines


def _wikilinks(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in WIKILINK_RE.finditer(text))


def _wikilink_target(path: Path) -> str:
    target = path.as_posix()
    if target.endswith(".md"):
        target = target[:-3]
    if target.startswith("Wiki/"):
        target = target[5:]
    return target.casefold()


def _resolve_link_target(target: str, path_targets: set[str]) -> Optional[str]:
    normalized = target.strip()
    if normalized.endswith(".md"):
        normalized = normalized[:-3]
    if normalized.startswith("Wiki/"):
        normalized = normalized[5:]
    folded = normalized.casefold()
    if folded in path_targets:
        return f"Wiki/{folded}.md"
    suffix_matches = [item for item in path_targets if item.endswith(f"/{folded}")]
    if len(suffix_matches) == 1:
        return f"Wiki/{suffix_matches[0]}.md"
    return None


def _is_external_vault_link(target: str) -> bool:
    first = target.strip().split("/", 1)[0]
    return first in {"Sources", "Memories", "raw"}


def _recent_log_entries(config: MemoryConfig, *, limit: int) -> list[str]:
    path = config.wiki_root / "log.md"
    if not path.exists():
        return []
    entries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## [")]
    return entries[-limit:]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()

