import pytest

from agent_memory.config import (
    ConfigError,
    ENV_AGENT_DEFAULT_RECALL_BUDGET,
    ENV_AGENT_TRUST_LEVEL,
    ENV_FRESHNESS_REFRESH_BEFORE_RECALL,
    ENV_FRESHNESS_REFRESH_BEFORE_SEARCH,
    ENV_SEMANTIC_BATCH_SIZE,
    ENV_SEMANTIC_DIMENSIONS,
    ENV_SEMANTIC_MIN_SIMILARITY,
    ENV_SEMANTIC_MODEL,
    ENV_SEMANTIC_PROVIDER,
    ENV_VAULT_PATH,
    load_config,
)
from agent_memory.vault import init_vault


def test_load_config_from_explicit_vault(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    config = load_config(vault)

    assert config.vault_path == vault.resolve()
    assert config.memory_root == vault.resolve() / "Memories"
    assert config.config_path == vault.resolve() / ".agent-memory" / "config.yaml"


def test_load_config_walks_up_from_child_path(tmp_path):
    vault = tmp_path / "memory-vault"
    child = vault / "Memories" / "decisions"
    init_vault(vault)

    config = load_config(start_path=child)

    assert config.vault_path == vault.resolve()


def test_load_config_uses_environment_vault(tmp_path, monkeypatch):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    monkeypatch.setenv(ENV_VAULT_PATH, str(vault))

    config = load_config()

    assert config.vault_path == vault.resolve()


def test_load_config_applies_semantic_environment_overrides(tmp_path, monkeypatch):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    monkeypatch.setenv(ENV_SEMANTIC_PROVIDER, " deterministic ")
    monkeypatch.setenv(ENV_SEMANTIC_MODEL, " deterministic-test-v1 ")
    monkeypatch.setenv(ENV_SEMANTIC_BATCH_SIZE, "7")
    monkeypatch.setenv(ENV_SEMANTIC_DIMENSIONS, "64")
    monkeypatch.setenv(ENV_SEMANTIC_MIN_SIMILARITY, "0.25")

    config = load_config(vault)

    assert config.semantic.provider == "deterministic"
    assert config.semantic.enabled is True
    assert config.semantic.model == "deterministic-test-v1"
    assert config.semantic.batch_size == 7
    assert config.semantic.dimensions == 64
    assert config.semantic.min_similarity == 0.25


def test_load_config_includes_agent_policy_defaults_and_overrides(tmp_path, monkeypatch):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    monkeypatch.setenv(ENV_AGENT_TRUST_LEVEL, "autonomous")
    monkeypatch.setenv(ENV_AGENT_DEFAULT_RECALL_BUDGET, "1800")

    config = load_config(vault)

    assert config.agent_policy.aliases == ["Toby", "Тоби", "tb"]
    assert config.agent_policy.trust_level == "autonomous"
    assert config.agent_policy.default_recall_budget == 1800
    assert config.agent_policy.min_active_confidence == 0.85
    assert config.recall_policies["default"].budget == 1200
    assert config.recall_policies["planning"].budget == 2000
    assert config.recall_policies["planning"].include_related is True


def test_load_config_includes_freshness_defaults_and_overrides(tmp_path, monkeypatch):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    monkeypatch.setenv(ENV_FRESHNESS_REFRESH_BEFORE_SEARCH, "false")
    monkeypatch.setenv(ENV_FRESHNESS_REFRESH_BEFORE_RECALL, "false")

    config = load_config(vault)

    assert config.index_freshness.enabled is True
    assert config.index_freshness.refresh_before_search is False
    assert config.index_freshness.refresh_before_recall is False


def test_invalid_config_schema_version_is_rejected(tmp_path):
    vault = tmp_path / "memory-vault"
    config_dir = vault / ".agent-memory"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("schema_version: 999\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="schema_version must be 1"):
        load_config(vault)
