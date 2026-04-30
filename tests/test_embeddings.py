import json

import pytest

from agent_memory.config import SemanticConfig
from agent_memory.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProviderError,
    LocalCommandEmbeddingProvider,
    provider_from_config,
)


class _CompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def test_provider_from_config_builds_deterministic_test_provider():
    provider = provider_from_config(
        SemanticConfig(provider="deterministic", model="deterministic-test-v1", dimensions=8)
    )

    assert provider.name == "deterministic"
    assert provider.model == "deterministic-test-v1"
    assert len(provider.embed(["fixture"])[0]) == 8


def test_provider_from_config_rejects_open_public_providers():
    for provider_name in ("openai", "ollama", "fastembed"):
        with pytest.raises(
            EmbeddingProviderError,
            match=f"unsupported semantic provider: {provider_name}",
        ):
            provider_from_config(SemanticConfig(provider=provider_name))


def test_local_command_provider_uses_generic_json_protocol(monkeypatch):
    payloads = []

    def fake_run(command, *, input, capture_output, check, encoding, timeout):
        payload = json.loads(input)
        payloads.append((command, payload, capture_output, check, encoding, timeout))
        return _CompletedProcess(
            json.dumps(
                {
                    "embeddings": [
                        [float(len(payloads)), float(index)]
                        for index, _text in enumerate(payload["texts"])
                    ]
                }
            )
        )

    monkeypatch.setattr("agent_memory.embeddings.subprocess.run", fake_run)

    provider = LocalCommandEmbeddingProvider(
        command=["embed-session"],
        model="same-session-model",
        batch_size=2,
        timeout_seconds=5,
    )

    vectors = provider.embed(["first", "second", "third"])

    assert vectors == [[1.0, 0.0], [1.0, 1.0], [2.0, 0.0]]
    assert payloads == [
        (
            ("embed-session",),
            {"model": "same-session-model", "texts": ["first", "second"]},
            True,
            True,
            "utf-8",
            5,
        ),
        (
            ("embed-session",),
            {"model": "same-session-model", "texts": ["third"]},
            True,
            True,
            "utf-8",
            5,
        ),
    ]


def test_deterministic_provider_is_test_only_fixture_quality():
    provider = DeterministicEmbeddingProvider()

    assert provider.name == "deterministic"
    assert provider.embed(["same"])[0] == provider.embed(["same"])[0]
    assert provider.embed(["same"])[0] != provider.embed(["different"])[0]
