"""Deterministic source and memory safety scanning helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

SCANNER_VERSION = 1

PROMPT_INJECTION_FLAG = "prompt_injection"
LIKELY_SECRET_FLAG = "likely_secret"
SECRET_SENSITIVITY_FLAG = "secret_sensitivity"
UNSAFE_SENSITIVITY_FLAG = "unsafe_sensitivity"
UNSAFE_METADATA_FLAG = "unsafe_metadata"

SAFETY_FLAG_ORDER = (
    PROMPT_INJECTION_FLAG,
    LIKELY_SECRET_FLAG,
    SECRET_SENSITIVITY_FLAG,
    UNSAFE_SENSITIVITY_FLAG,
    UNSAFE_METADATA_FLAG,
)
UNSAFE_RECALL_FLAGS = frozenset(SAFETY_FLAG_ORDER)

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+"
            r"(?:instructions|directions|rules|system\s+message)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "disregard_system_instructions",
        re.compile(
            r"\bdisregard\s+(?:the\s+)?(?:system|developer|previous|prior)"
            r"(?:\s+and\s+(?:system|developer|previous|prior))*\s+instructions\b",
            re.IGNORECASE,
        ),
    ),
    (
        "reveal_secrets",
        re.compile(r"\breveal\s+(?:the\s+)?(?:system\s+prompt|secrets?|credentials?)\b", re.IGNORECASE),
    ),
    (
        "exfiltrate_credentials",
        re.compile(r"\bexfiltrate(?:\s+(?:data|secrets?|credentials?|tokens?))?\b", re.IGNORECASE),
    ),
    (
        "send_credentials",
        re.compile(r"\bsend\s+(?:the\s+)?(?:credentials|tokens?|api\s+keys?|secrets?)\b", re.IGNORECASE),
    ),
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key_block",
        re.compile(r"-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----", re.IGNORECASE),
    ),
    (
        "secret_assignment",
        re.compile(
            r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret|password|passwd)\b"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=:$-]{8,}",
            re.IGNORECASE,
        ),
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    ),
    (
        "common_service_token",
        re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b|\bgh[psou]_[A-Za-z0-9_]{20,}\b"),
    ),
)

_UNSAFE_METADATA_VALUES = {"unsafe", "malicious", "quarantined"}


@dataclass(frozen=True)
class SafetyFinding:
    """One deterministic safety finding without exposing secret values."""

    flag: str
    kind: str
    pattern: str
    field: Optional[str] = None
    excerpt: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "flag": self.flag,
            "kind": self.kind,
            "pattern": self.pattern,
        }
        if self.field is not None:
            payload["field"] = self.field
        if self.excerpt is not None:
            payload["excerpt"] = self.excerpt
        return payload


@dataclass(frozen=True)
class SafetyScanResult:
    """Aggregated scanner result suitable for JSON payloads and frontmatter."""

    risk_flags: tuple[str, ...]
    findings: tuple[SafetyFinding, ...]

    @property
    def has_risk(self) -> bool:
        return bool(self.risk_flags)

    @property
    def blocks_default_recall(self) -> bool:
        return has_unsafe_recall_risk(self.risk_flags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner_version": SCANNER_VERSION,
            "risk_flags": list(self.risk_flags),
            "findings": [finding.to_dict() for finding in self.findings],
            "blocks_default_recall": self.blocks_default_recall,
        }


def scan_text(text: Optional[str], *, field: Optional[str] = None) -> SafetyScanResult:
    """Scan text for high-signal prompt-injection and likely-secret patterns."""

    cleaned = text or ""
    findings: list[SafetyFinding] = []
    for name, pattern in _PROMPT_INJECTION_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            findings.append(
                SafetyFinding(
                    flag=PROMPT_INJECTION_FLAG,
                    kind="prompt_injection",
                    pattern=name,
                    field=field,
                    excerpt=_excerpt(cleaned, match.start(), match.end()),
                )
            )
            break
    for name, pattern in _SECRET_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            findings.append(
                SafetyFinding(
                    flag=LIKELY_SECRET_FLAG,
                    kind="likely_secret",
                    pattern=name,
                    field=field,
                    excerpt="[redacted]",
                )
            )
            break
    return _result(findings)


def scan_metadata(metadata: Optional[Mapping[str, Any]]) -> SafetyScanResult:
    """Scan existing structured metadata for unsafe source lifecycle signals."""

    findings: list[SafetyFinding] = []
    for key, raw_value in (metadata or {}).items():
        value = _normalized_value(raw_value)
        if value is None:
            continue
        if key == "sensitivity" and value == "secret":
            findings.append(
                SafetyFinding(
                    flag=SECRET_SENSITIVITY_FLAG,
                    kind="metadata",
                    pattern="sensitivity:secret",
                    field=key,
                )
            )
        elif key == "sensitivity" and value == "unsafe":
            findings.append(
                SafetyFinding(
                    flag=UNSAFE_SENSITIVITY_FLAG,
                    kind="metadata",
                    pattern="sensitivity:unsafe",
                    field=key,
                )
            )
        elif key in {"status", "quality", "source_quality", "safety_status"} and value in _UNSAFE_METADATA_VALUES:
            findings.append(
                SafetyFinding(
                    flag=UNSAFE_METADATA_FLAG,
                    kind="metadata",
                    pattern=f"{key}:{value}",
                    field=key,
                )
            )
    return _result(findings)


def scan_source_material(
    *,
    content: Optional[str],
    extract: Optional[str],
    metadata: Optional[Mapping[str, Any]] = None,
) -> SafetyScanResult:
    """Scan source content, extract text, and source metadata as one result."""

    return merge_scan_results(
        scan_text(content, field="content"),
        scan_text(extract, field="extract"),
        scan_metadata(metadata),
    )


def merge_scan_results(*results: SafetyScanResult) -> SafetyScanResult:
    """Merge scanner results while preserving stable risk flag order."""

    findings: list[SafetyFinding] = []
    for result in results:
        findings.extend(result.findings)
    return _result(findings)


def normalize_risk_flags(values: Optional[Iterable[Any]]) -> tuple[str, ...]:
    """Normalize persisted or caller-supplied risk flags."""

    if values is None:
        return ()
    if isinstance(values, str):
        raw_values: Iterable[Any] = [values]
    else:
        raw_values = values
    flags = []
    for value in raw_values:
        cleaned = str(value).strip().lower()
        if cleaned:
            flags.append(cleaned)
    return _ordered_flags(flags)


def has_unsafe_recall_risk(flags: Iterable[str]) -> bool:
    """Return true when flags should be excluded from default recall/profile use."""

    return bool(set(normalize_risk_flags(flags)) & UNSAFE_RECALL_FLAGS)


def _result(findings: Iterable[SafetyFinding]) -> SafetyScanResult:
    selected_findings = tuple(findings)
    flags = _ordered_flags(finding.flag for finding in selected_findings)
    return SafetyScanResult(risk_flags=flags, findings=selected_findings)


def _ordered_flags(flags: Iterable[str]) -> tuple[str, ...]:
    selected = {str(flag).strip().lower() for flag in flags if str(flag).strip()}
    ordered = [flag for flag in SAFETY_FLAG_ORDER if flag in selected]
    ordered.extend(sorted(selected - set(SAFETY_FLAG_ORDER)))
    return tuple(ordered)


def _normalized_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    cleaned = str(value).strip().lower()
    return cleaned or None


def _excerpt(text: str, start: int, end: int, *, radius: int = 48) -> str:
    prefix = max(0, start - radius)
    suffix = min(len(text), end + radius)
    excerpt = re.sub(r"\s+", " ", text[prefix:suffix]).strip()
    if prefix > 0:
        excerpt = f"...{excerpt}"
    if suffix < len(text):
        excerpt = f"{excerpt}..."
    return excerpt


__all__ = [
    "LIKELY_SECRET_FLAG",
    "PROMPT_INJECTION_FLAG",
    "SCANNER_VERSION",
    "SECRET_SENSITIVITY_FLAG",
    "UNSAFE_METADATA_FLAG",
    "UNSAFE_RECALL_FLAGS",
    "UNSAFE_SENSITIVITY_FLAG",
    "SafetyFinding",
    "SafetyScanResult",
    "has_unsafe_recall_risk",
    "merge_scan_results",
    "normalize_risk_flags",
    "scan_metadata",
    "scan_source_material",
    "scan_text",
]
