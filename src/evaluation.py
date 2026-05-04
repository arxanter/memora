"""Deterministic fixture-backed evaluation harness for Memora."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import yaml

from brief import brief_memory
from config import load_config
from indexer import reindex_vault
from lifecycle import review_queue
from recall import recall_memory
from retrieval import SearchFilters, search_memory
from sync import detect_sync_conflicts
from vault import doctor_report

PathLike = Union[Path, str]


@dataclass(frozen=True)
class EvaluationCaseResult:
    """Result for one deterministic evaluation case."""

    case_id: str
    mode: str
    passed: bool
    included_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    unexpected_ids: tuple[str, ...]
    warning_matches: tuple[str, ...]
    missing_warnings: tuple[str, ...]
    used_tokens_estimate: int
    max_tokens: Optional[int]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "mode": self.mode,
            "passed": self.passed,
            "included_ids": list(self.included_ids),
            "missing_ids": list(self.missing_ids),
            "unexpected_ids": list(self.unexpected_ids),
            "warning_matches": list(self.warning_matches),
            "missing_warnings": list(self.missing_warnings),
            "used_tokens_estimate": self.used_tokens_estimate,
            "max_tokens": self.max_tokens,
            "details": self.details,
        }


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregated evaluation report for a fixture or case file."""

    spec_path: Path
    source_vault_path: Path
    working_vault_path: Path
    case_results: tuple[EvaluationCaseResult, ...]
    reindex: dict[str, Any]

    @property
    def ok(self) -> bool:
        return all(result.passed for result in self.case_results)

    @property
    def case_count(self) -> int:
        return len(self.case_results)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.case_results if not result.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "implemented": True,
            "spec_path": str(self.spec_path),
            "source_vault_path": str(self.source_vault_path),
            "working_vault_path": str(self.working_vault_path),
            "case_count": self.case_count,
            "failed_count": self.failed_count,
            "reindex": self.reindex,
            "cases": [result.to_dict() for result in self.case_results],
        }


def run_evaluation(fixture_or_file: PathLike, *, keep_working_vault: bool = False) -> EvaluationReport:
    """Run a deterministic evaluation spec against a throwaway copy of its vault."""

    spec_path = _resolve_spec_path(fixture_or_file)
    spec = _load_spec(spec_path)
    source_vault = (spec_path.parent / str(spec.get("vault", "."))).resolve()
    if not source_vault.exists():
        raise ValueError(f"evaluation vault not found: {source_vault}")

    temp_dir = Path(tempfile.mkdtemp(prefix="memora-eval-"))
    working_vault = temp_dir / source_vault.name
    shutil.copytree(source_vault, working_vault, ignore=_copy_ignore)

    try:
        config = load_config(working_vault)
        reindex_payload = reindex_vault(config, clean=True).to_dict()
        results = tuple(_run_case(config, case) for case in _cases(spec))
        return EvaluationReport(
            spec_path=spec_path,
            source_vault_path=source_vault,
            working_vault_path=working_vault,
            case_results=results,
            reindex=reindex_payload,
        )
    finally:
        if not keep_working_vault:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _run_case(config: Any, case: Mapping[str, Any]) -> EvaluationCaseResult:
    case_id = str(case["id"])
    mode = str(case.get("mode", "recall"))
    query = str(case.get("query", "")).strip()
    filters = SearchFilters.from_mapping(case.get("filters"))
    include_related = bool(case.get("include_related", False))
    semantic = case.get("semantic")
    semantic_value = None if semantic is None else bool(semantic)
    max_tokens = _optional_int(case.get("max_tokens"))

    payload: dict[str, Any]
    ids: tuple[str, ...]
    warnings: tuple[str, ...]
    used_tokens = 0

    if mode == "search":
        response = search_memory(
            config,
            query,
            filters=filters,
            include_related=include_related,
            semantic=semantic_value,
            limit=int(case.get("limit", 10)),
        ).to_dict()
        payload = response
        ids = tuple(result["id"] for result in response["results"])
        warnings = ()
    elif mode == "recall":
        response = recall_memory(
            config,
            query,
            filters=filters,
            budget=max_tokens or int(case.get("budget", 1200)),
            include_related=include_related,
            semantic=semantic_value,
        ).to_dict()
        payload = response
        ids = tuple(chunk["id"] for chunk in response["chunks"])
        used_tokens = int(response["used_tokens_estimate"])
        warnings = ()
    elif mode == "brief":
        response = brief_memory(
            config,
            query,
            filters=filters,
            budget=max_tokens or int(case.get("budget", 1200)),
            include_related=include_related,
            semantic=semantic_value,
        ).to_dict()
        payload = response
        ids = _brief_ids(response)
        used_tokens = int(response["used_tokens_estimate"])
        warnings = _brief_warnings(response)
    elif mode == "review":
        response = review_queue(config).to_dict()
        payload = response
        ids = tuple(item["id"] for item in response["items"])
        warnings = ()
    elif mode == "conflicts":
        response = detect_sync_conflicts(config).to_dict()
        payload = response
        ids = tuple(str(issue.get("id", issue["kind"])) for issue in response["issues"])
        warnings = tuple(issue["kind"] for issue in response["issues"])
    elif mode == "doctor":
        response = doctor_report(config)
        payload = response
        ids = tuple(str(issue.get("id", issue.get("from_id", issue["kind"]))) for issue in response["issues"])
        warnings = tuple(warning["message"] for warning in response["warnings"])
    else:
        raise ValueError(f"unsupported evaluation mode: {mode}")

    expected_included = tuple(str(item) for item in case.get("expected_include", ()))
    expected_excluded = tuple(str(item) for item in case.get("expected_exclude", ()))
    expected_warnings = tuple(str(item) for item in case.get("expected_warnings", ()))
    id_set = set(ids)
    missing_ids = tuple(item for item in expected_included if item not in id_set)
    unexpected_ids = tuple(item for item in expected_excluded if item in id_set)
    warning_matches = tuple(item for item in expected_warnings if _contains_warning(warnings, item))
    missing_warnings = tuple(item for item in expected_warnings if item not in warning_matches)
    token_failure = max_tokens is not None and used_tokens > max_tokens
    explainability_failure = bool(case.get("expect_explainable", False)) and not _has_explainability(payload, mode)
    passed = not missing_ids and not unexpected_ids and not missing_warnings and not token_failure and not explainability_failure

    return EvaluationCaseResult(
        case_id=case_id,
        mode=mode,
        passed=passed,
        included_ids=ids,
        missing_ids=missing_ids,
        unexpected_ids=unexpected_ids,
        warning_matches=warning_matches,
        missing_warnings=missing_warnings,
        used_tokens_estimate=used_tokens,
        max_tokens=max_tokens,
        details={
            "query": query,
            "filters": filters.to_dict(),
            "include_related": include_related,
            "token_failure": token_failure,
            "explainability_failure": explainability_failure,
        },
    )


def _resolve_spec_path(fixture_or_file: PathLike) -> Path:
    path = Path(fixture_or_file).expanduser().resolve()
    if path.is_dir():
        path = path / "evaluation.yaml"
    if not path.exists():
        raise ValueError(f"evaluation spec not found: {path}")
    return path


def _load_spec(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("evaluation spec must be a YAML mapping")
    return loaded


def _cases(spec: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation spec must include a non-empty cases list")
    return tuple(_case(case) for case in cases)


def _case(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not value.get("id"):
        raise ValueError("each evaluation case must be a mapping with an id")
    return value


def _brief_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for section_items in payload["sections"].values():
        for item in section_items:
            source_id = item.get("source_id")
            if source_id:
                ids.append(str(source_id))
    for citation in payload["citations"]:
        ids.append(str(citation["id"]))
    return tuple(dict.fromkeys(ids))


def _brief_warnings(payload: Mapping[str, Any]) -> tuple[str, ...]:
    warning_sections = (
        payload["sections"].get("warnings", ()),
        payload["sections"].get("open_questions", ()),
    )
    return tuple(str(item["text"]) for section in warning_sections for item in section)


def _contains_warning(warnings: tuple[str, ...], expected: str) -> bool:
    return any(expected in warning for warning in warnings)


def _has_explainability(payload: Mapping[str, Any], mode: str) -> bool:
    if mode == "search":
        return all(result.get("citation") and result.get("score_breakdown") for result in payload["results"])
    if mode == "recall":
        return all(chunk.get("citation") and chunk.get("score_breakdown") for chunk in payload["chunks"])
    if mode == "brief":
        return bool(payload.get("citations")) and "recall" in payload
    return True


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    del directory
    ignored = {"index.sqlite"}
    ignored.update(name for name in names if name in {"cache", "embeddings", "locks"})
    return ignored


__all__ = [
    "EvaluationCaseResult",
    "EvaluationReport",
    "run_evaluation",
]
