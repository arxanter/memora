from pathlib import Path

import pytest

from config import (
    ConfigError,
    ENV_AGENT_DEFAULT_RECALL_BUDGET,
    ENV_AGENT_TRUST_LEVEL,
    ENV_FRESHNESS_REFRESH_BEFORE_RECALL,
    ENV_FRESHNESS_REFRESH_BEFORE_SEARCH,
    ENV_PROFILE_ENABLED,
    ENV_PROFILE_INJECT_BY_DEFAULT,
    ENV_PROFILE_PROJECT_BUDGET,
    ENV_PROFILE_USER_BUDGET,
    ENV_SEMANTIC_BATCH_SIZE,
    ENV_SEMANTIC_DIMENSIONS,
    ENV_SEMANTIC_MIN_SIMILARITY,
    ENV_SEMANTIC_MODEL,
    ENV_SEMANTIC_PROVIDER,
    ENV_VAULT_PATH,
    config_to_dict,
    load_config,
    set_agent_aliases,
)
from vault import init_vault


ROOT = Path(__file__).resolve().parents[1]


def test_load_config_from_explicit_vault(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    config = load_config(vault)

    assert config.vault_path == vault.resolve()
    assert config.raw_root == vault.resolve() / "raw"
    assert config.memory_root == vault.resolve() / "Memories"
    assert config.home_path == vault.resolve()
    assert config.config_path == vault.resolve() / "config.yaml"
    assert config.index_file == vault.resolve() / "state" / "index.sqlite"
    assert config.semantic.provider == "fastembed"
    assert config.semantic.model == "BAAI/bge-small-en-v1.5"


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

    assert config.agent_policy.aliases == ["Remi", "Рэми", "Реми"]
    assert config.agent_policy.enabled is True
    assert config.agent_policy.auto_recall is True
    assert config.agent_policy.session_capture is True
    assert config.agent_policy.trust_level == "autonomous"
    assert config.agent_policy.default_recall_budget == 1800
    assert config.agent_policy.min_active_confidence == 0.85
    assert config.recall_policies["default"].budget == 1200
    assert config.recall_policies["default"].include_profile is True
    assert config.recall_policies["coding"].include_profile is True
    assert config.recall_policies["planning"].budget == 2000
    assert config.recall_policies["planning"].include_related is True
    assert config.recall_policies["planning"].include_profile is True
    assert config.recall_policies["review"].include_profile is False


def test_set_agent_aliases_updates_config_yaml(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    updated = set_agent_aliases(vault, ["Taylor", "Тейлор"])

    assert updated == ["Taylor", "Тейлор"]
    assert load_config(vault).agent_policy.aliases == ["Taylor", "Тейлор"]


def test_load_config_applies_recall_policy_include_profile_yaml_overrides(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config_path = vault / "config.yaml"
    config_path.write_text(
        """
schema_version: 1
recall_policies:
  default:
    budget: 1200
    include_profile: false
  review:
    budget: 2400
    include_pending: true
    include_profile: true
  custom:
    budget: 700
""".lstrip(),
        encoding="utf-8",
    )

    config = load_config(vault)

    assert config.recall_policies["default"].include_profile is False
    assert config.recall_policies["review"].include_profile is True
    assert config.recall_policies["custom"].include_profile is True


def test_load_config_preserves_review_include_profile_default_for_old_yaml(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    config_path = vault / "config.yaml"
    config_path.write_text(
        """
schema_version: 1
recall_policies:
  review:
    budget: 2400
    include_pending: true
""".lstrip(),
        encoding="utf-8",
    )

    config = load_config(vault)

    assert config.recall_policies["review"].include_profile is False


def test_load_config_includes_freshness_defaults_and_overrides(tmp_path, monkeypatch):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    monkeypatch.setenv(ENV_FRESHNESS_REFRESH_BEFORE_SEARCH, "false")
    monkeypatch.setenv(ENV_FRESHNESS_REFRESH_BEFORE_RECALL, "false")

    config = load_config(vault)

    assert config.index_freshness.enabled is True
    assert config.index_freshness.refresh_before_search is False
    assert config.index_freshness.refresh_before_recall is False


def test_load_config_includes_profile_defaults_and_summary(tmp_path):
    vault = tmp_path / "memory-vault"
    init_vault(vault)

    config = load_config(vault)
    summary = config_to_dict(config)

    assert config.profile.enabled is True
    assert config.profile.user_budget == 500
    assert config.profile.project_budget == 700
    assert config.profile.inject_by_default is False
    assert summary["profile"] == {
        "enabled": True,
        "user_budget": 500,
        "project_budget": 700,
        "inject_by_default": False,
    }


def test_sample_vault_config_loads_with_cli_first_defaults():
    sample_vault = ROOT / "examples" / "sample-vault"

    config = load_config(sample_vault)
    summary = config_to_dict(config)

    assert config.default_project == "memora"
    assert config.agent_default_status == "pending"
    assert config.agent_policy.trust_level == "review"
    assert config.recall_policies["planning"].include_related is True
    assert config.recall_policies["review"].include_pending is True
    assert config.profile.enabled is True
    assert config.index_freshness.refresh_before_search is True
    assert summary["semantic"]["provider"] is None


def test_load_config_applies_profile_environment_overrides(tmp_path, monkeypatch):
    vault = tmp_path / "memory-vault"
    init_vault(vault)
    monkeypatch.setenv(ENV_PROFILE_ENABLED, "false")
    monkeypatch.setenv(ENV_PROFILE_USER_BUDGET, "321")
    monkeypatch.setenv(ENV_PROFILE_PROJECT_BUDGET, "654")
    monkeypatch.setenv(ENV_PROFILE_INJECT_BY_DEFAULT, "true")

    config = load_config(vault)

    assert config.profile.enabled is False
    assert config.profile.user_budget == 321
    assert config.profile.project_budget == 654
    assert config.profile.inject_by_default is True


def test_invalid_config_schema_version_is_rejected(tmp_path):
    vault = tmp_path / "memory-vault"
    vault.mkdir(parents=True)
    (vault / "config.yaml").write_text("schema_version: 999\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="schema_version must be 1"):
        load_config(vault)
