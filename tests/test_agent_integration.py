from config import AgentPolicyConfig

from agent_integration import (
    AgentClient,
    IntegrationScope,
    TargetSupport,
    agent_status_payload,
    agent_targets_payload,
    managed_block_metadata,
    managed_content_hash,
    plan_managed_agent_write,
    replace_managed_block,
    render_agent_rules,
    render_managed_block,
    resolve_integration_target,
    select_agent_clients,
)

_DEFAULT_AGENT_ALIASES = AgentPolicyConfig().aliases


def test_project_targets_match_existing_agent_rule_defaults(tmp_path):
    project = tmp_path / "project"

    assert resolve_integration_target("cursor", project_path=project).path == (
        project.resolve() / ".cursor" / "rules" / "memora.mdc"
    )
    assert resolve_integration_target("claude", project_path=project).path == project.resolve() / "CLAUDE.md"
    assert resolve_integration_target("codex", project_path=project).path == project.resolve() / "AGENTS.md"
    assert resolve_integration_target("agents", project_path=project).path == project.resolve() / "AGENTS.md"


def test_select_agent_clients_all_skips_duplicate_agents_target():
    assert select_agent_clients("all") == (
        AgentClient.CURSOR,
        AgentClient.CLAUDE,
        AgentClient.CODEX,
    )


def test_render_agent_rules_preserves_phase_one_content(tmp_path):
    content = render_agent_rules(
        "cursor",
        vault_path=tmp_path / "vault",
        project="memora",
        agent_aliases=_DEFAULT_AGENT_ALIASES,
    )

    assert content.startswith("---\ndescription:")
    assert "CLI-first" in content
    assert "CLI-only for agents" in content
    assert 'memora build-context "<task>"' in content
    assert f'--vault "{tmp_path / "vault"}"' in content
    assert '--project "memora"' in content
    assert "prefer the default compact agent output" in content
    assert "memora search \"<query>\"" in content


def test_render_agent_rules_contains_strict_vault_and_remi_policy():
    content = render_agent_rules(
        "codex",
        vault_path=None,
        project="memora",
        agent_aliases=_DEFAULT_AGENT_ALIASES,
    )

    assert "Do not read, write, edit, delete, or migrate Memora vault files directly" in content
    assert "docs/cli-agent-reference.md" in content
    for private_path in (
        "`Memories/`",
        "`Sources/`",
        "`Briefs/`",
        "`raw/`",
        "`.memora/index.sqlite`",
        "cache",
        "embeddings",
        "locks",
        "schema files",
    ):
        assert private_path in content
    assert "If the CLI lacks an operation, stop and report the missing command" in content
    assert "Remi intent routing examples" in content
    assert "Remi, show current facts about <topic>" in content
    assert "Рэми, что мы решили по <topic>" in content
    assert "Реми, что мы решили по <topic>" in content
    assert "Remi, save this fact/decision/preference" in content
    assert "Remi, review pending memory" in content
    assert "Рэми, актуализируй память по <topic>" in content
    assert "Реми, актуализируй память по <topic>" in content
    assert "Remi, analyze this source and save it" in content
    assert "do not narrate every `memora ...` call" in content


def test_user_scope_targets_use_real_global_files_when_supported(tmp_path):
    cursor_target = resolve_integration_target(
        "cursor",
        scope=IntegrationScope.USER,
        home=tmp_path,
    )
    claude_target = resolve_integration_target(
        "claude",
        scope=IntegrationScope.USER,
        home=tmp_path,
    )
    codex_target = resolve_integration_target(
        "codex",
        scope=IntegrationScope.USER,
        home=tmp_path,
    )

    assert cursor_target.client == AgentClient.CURSOR
    assert cursor_target.scope == IntegrationScope.USER
    assert cursor_target.support == TargetSupport.FALLBACK
    assert cursor_target.path == tmp_path.resolve() / ".memora" / "integrations" / "cursor-memora.mdc"
    assert claude_target.client == AgentClient.CLAUDE
    assert claude_target.support == TargetSupport.SUPPORTED
    assert claude_target.path == tmp_path.resolve() / ".claude" / "CLAUDE.md"
    assert codex_target.client == AgentClient.CODEX
    assert codex_target.support == TargetSupport.SUPPORTED
    assert codex_target.path == tmp_path.resolve() / ".codex" / "AGENTS.md"


def test_managed_block_helpers_round_trip_metadata():
    content = "Memora instructions"
    block = render_managed_block(content, template_version="agent-rules-test")

    assert managed_block_metadata(block) == {
        "template_version": "agent-rules-test",
        "content_hash": managed_content_hash(content),
    }
    assert managed_block_metadata("plain user instructions") == {}


def test_agent_targets_payload_all_skips_duplicate_agents_target(tmp_path):
    project = tmp_path / "project"

    payload = agent_targets_payload(client="all", scope="project", project=project)

    assert payload["command"] == "agent targets"
    assert [target["client"] for target in payload["targets"]] == ["cursor", "claude", "codex"]
    assert [target["path"] for target in payload["targets"]] == [
        str(project.resolve() / ".cursor" / "rules" / "memora.mdc"),
        str(project.resolve() / "CLAUDE.md"),
        str(project.resolve() / "AGENTS.md"),
    ]


def test_plan_managed_agent_write_blocks_unmanaged_existing_target(tmp_path):
    target_path = tmp_path / "AGENTS.md"
    target_path.write_text("user instructions\n", encoding="utf-8")
    target = resolve_integration_target("codex", project_path=tmp_path, target=target_path)

    plan = plan_managed_agent_write(
        target_info=target,
        content=render_agent_rules(
            "codex",
            vault_path=None,
            project="project",
            agent_aliases=_DEFAULT_AGENT_ALIASES,
        ),
        dry_run=True,
        force=False,
    )

    assert plan["action"] == "blocked"
    assert plan["blocked"] is True
    assert plan["needs_manual_merge"] is True
    assert plan["would_write"] is False
    assert target_path.read_text(encoding="utf-8") == "user instructions\n"


def test_replace_managed_block_preserves_surrounding_user_content():
    old = "Intro\n\n" + render_managed_block("old") + "\nOutro\n"
    new_block = render_managed_block("new")

    updated = replace_managed_block(old, new_block)

    assert "Intro" in updated
    assert "old" not in updated
    assert "new" in updated
    assert "Outro" in updated


def test_agent_status_detects_managed_current_target(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    content = render_agent_rules(
        "codex",
        vault_path=None,
        project="project",
        agent_aliases=_DEFAULT_AGENT_ALIASES,
    )
    target.write_text(render_managed_block(content), encoding="utf-8")

    payload = agent_status_payload(client="codex", scope="project", project=project)

    assert payload["command"] == "agent status"
    assert payload["results"][0]["status"] == "installed"
    assert payload["results"][0]["managed"] is True
    assert payload["results"][0]["needs_update"] is False
