"""Embedding provider abstractions for optional semantic search."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from typing import Callable, Protocol, Sequence

from agent_memory.config import SemanticConfig


class EmbeddingProvider(Protocol):
    """Small provider interface used by retrieval without heavy dependencies."""

    @property
    def name(self) -> str:
        """Stable provider identifier for reporting and diagnostics."""

    @property
    def model(self) -> str:
        """Stable model/cache key for vectors produced by this provider."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector for each input text."""


class EmbeddingProviderError(RuntimeError):
    """Raised when a configured embedding provider cannot produce vectors."""


class DeterministicEmbeddingProvider:
    """Dependency-free provider for tests and deterministic fixtures only."""

    def __init__(self, *, model: str = "deterministic-test-v1", dimensions: int = 32) -> None:
        self._model = model
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return "deterministic"

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_normalize(_hashed_bow_vector(text, dimensions=self._dimensions)) for text in texts]


class LocalCommandEmbeddingProvider:
    """Call a local embedding command using a simple JSON stdin/stdout contract."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        model: str,
        timeout_seconds: float = 30.0,
        batch_size: int = 32,
    ) -> None:
        if not command:
            raise EmbeddingProviderError("local-command semantic provider requires a command")
        self._command = tuple(command)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size

    @property
    def name(self) -> str:
        return "local-command"

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return _embed_in_batches(texts, self._batch_size, self._embed_batch)

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "texts": list(texts)})
        try:
            completed = subprocess.run(
                self._command,
                input=payload,
                capture_output=True,
                check=True,
                encoding="utf-8",
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise EmbeddingProviderError(f"local embedding command failed: {exc}") from exc

        try:
            decoded = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise EmbeddingProviderError("local embedding command did not return JSON") from exc

        vectors = decoded.get("embeddings") if isinstance(decoded, dict) else decoded
        if not isinstance(vectors, list):
            raise EmbeddingProviderError("local embedding command must return an embeddings list")
        return _validate_vectors(vectors, expected_count=len(texts))


def provider_from_config(config: SemanticConfig) -> EmbeddingProvider:
    """Build the configured provider, or raise if semantic search is disabled."""

    if config.provider is None:
        raise EmbeddingProviderError("semantic search is disabled; configure semantic.provider first")
    if config.provider == "deterministic":
        return DeterministicEmbeddingProvider(
            model=config.model,
            dimensions=config.dimensions or 32,
        )
    if config.provider == "local-command":
        if config.command is None:
            raise EmbeddingProviderError("semantic provider local-command requires semantic.command")
        return LocalCommandEmbeddingProvider(
            command=config.command,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            batch_size=config.batch_size,
        )
    raise EmbeddingProviderError(f"unsupported semantic provider: {config.provider}")


def serialize_vector(vector: Sequence[float]) -> str:
    return json.dumps([float(value) for value in vector], separators=(",", ":"))


def deserialize_vector(value: str) -> list[float]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise EmbeddingProviderError("stored embedding vector is not valid JSON") from exc
    return _validate_vector(decoded)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _embed_in_batches(
    texts: Sequence[str],
    batch_size: int,
    embed_batch: Callable[[Sequence[str]], list[list[float]]],
) -> list[list[float]]:
    selected_texts = list(texts)
    if not selected_texts:
        return []

    vectors: list[list[float]] = []
    for start in range(0, len(selected_texts), batch_size):
        vectors.extend(embed_batch(selected_texts[start : start + batch_size]))
    return vectors


def _hashed_bow_vector(text: str, *, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    return vector


def _tokens(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    return [_normalize_token(token) for token in tokens]


def _normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return [0.0 for _ in vector]
    return [value / norm for value in vector]


def _validate_vectors(vectors: list[object], *, expected_count: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingProviderError(
            f"embedding provider returned {len(vectors)} vectors for {expected_count} texts"
        )
    return [_validate_vector(vector) for vector in vectors]


def _validate_vector(value: object) -> list[float]:
    if not isinstance(value, list) or not value:
        raise EmbeddingProviderError("embedding vectors must be non-empty lists")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise EmbeddingProviderError("embedding vectors must contain only numbers") from exc


__all__ = [
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "LocalCommandEmbeddingProvider",
    "cosine_similarity",
    "deserialize_vector",
    "provider_from_config",
    "serialize_vector",
]
