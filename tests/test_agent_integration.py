from agent_memory.agent_integration import (
    AgentClient,
    IntegrationScope,
    TargetSupport,
    agent_status_payload,
    agent_install_command_clients,
    agent_targets_payload,
    install_command_plan,
    managed_block_metadata,
    managed_content_hash,
    plan_managed_agent_write,
    replace_managed_block,
    render_agent_rules,
    render_managed_block,
    resolve_integration_target,
)


def test_project_targets_match_existing_agent_rule_defaults(tmp_path):
    project = tmp_path / "project"

    assert resolve_integration_target("cursor", project_path=project).path == (
        project.resolve() / ".cursor" / "rules" / "agent-memory.mdc"
    )
    assert resolve_integration_target("claude", project_path=project).path == project.resolve() / "CLAUDE.md"
    assert resolve_integration_target("codex", project_path=project).path == project.resolve() / "AGENTS.md"
    assert resolve_integration_target("agents", project_path=project).path == project.resolve() / "AGENTS.md"


def test_all_install_command_clients_skip_duplicate_agents_target():
    assert agent_install_command_clients("all") == (
        AgentClient.CURSOR,
        AgentClient.CLAUDE,
        AgentClient.CODEX,
    )


def test_install_command_plan_renders_existing_compatibility_command(tmp_path):
    project = tmp_path / "project with spaces"
    vault = tmp_path / "vault"

    plan = install_command_plan(
        client="codex",
        project_path=project,
        vault_path=vault,
        force=True,
        dry_run_first=True,
    )

    assert plan["client"] == "codex"
    assert plan["target_path"] == str(project / "AGENTS.md")
    assert "memory install-agent-rules --client codex" in plan["install_command"]
    assert f"--project '{project}'" in plan["install_command"]
    assert f"--vault {vault}" in plan["install_command"]
    assert "--force" in plan["install_command"]
    assert plan["dry_run_command"].endswith("--force --dry-run")


def test_render_agent_rules_preserves_phase_one_content(tmp_path):
    content = render_agent_rules("cursor", vault_path=tmp_path / "vault", project="memory-project")

    assert content.startswith("---\ndescription:")
    assert "CLI-first" in content
    assert "CLI-only for agents" in content
    assert 'memory build-context "<task>"' in content
    assert f'--vault "{tmp_path / "vault"}"' in content
    assert '--project "memory-project"' in content
    assert "Use only `memory ... --json` commands" in content


def test_render_agent_rules_contains_strict_vault_and_toby_policy():
    content = render_agent_rules("codex", vault_path=None, project="memory-project")

    assert "Do not read, write, edit, delete, or migrate Agent Memory vault files directly" in content
    for private_path in (
        "`Memories/`",
        "`Sources/`",
        "`Briefs/`",
        "`Profiles/`",
        "`Synthesis/`",
        "`raw/`",
        "`.agent-memory/index.sqlite`",
        "cache",
        "embeddings",
        "locks",
        "schema files",
    ):
        assert private_path in content
    assert "If the CLI lacks an operation, stop and report the missing command" in content
    assert "Toby intent routing examples" in content
    assert "Toby, show current facts about <topic>" in content
    assert "Тоби, что мы решили по <topic>" in content
    assert "Toby, save this fact/decision/preference" in content
    assert "Toby, review pending memory" in content
    assert "Тоби, актуализируй память по <topic>" in content
    assert "Toby, analyze this source and save it" in content
    assert "do not narrate every `memory ... --json` call" in content


def test_user_scope_targets_are_safe_fallbacks(tmp_path):
    target = resolve_integration_target(
        "codex",
        scope=IntegrationScope.USER,
        home=tmp_path,
    )

    assert target.client == AgentClient.CODEX
    assert target.scope == IntegrationScope.USER
    assert target.support == TargetSupport.FALLBACK
    assert target.path == tmp_path.resolve() / ".agent-memory" / "integrations" / "codex" / "AGENTS.md"


def test_managed_block_helpers_round_trip_metadata():
    content = "Agent Memory instructions"
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
        str(project.resolve() / ".cursor" / "rules" / "agent-memory.mdc"),
        str(project.resolve() / "CLAUDE.md"),
        str(project.resolve() / "AGENTS.md"),
    ]


def test_plan_managed_agent_write_blocks_unmanaged_existing_target(tmp_path):
    target_path = tmp_path / "AGENTS.md"
    target_path.write_text("user instructions\n", encoding="utf-8")
    target = resolve_integration_target("codex", project_path=tmp_path, target=target_path)

    plan = plan_managed_agent_write(
        target_info=target,
        content=render_agent_rules("codex", vault_path=None, project="project"),
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
    content = render_agent_rules("codex", vault_path=None, project="project")
    target.write_text(render_managed_block(content), encoding="utf-8")

    payload = agent_status_payload(client="codex", scope="project", project=project)

    assert payload["command"] == "agent status"
    assert payload["results"][0]["status"] == "installed"
    assert payload["results"][0]["managed"] is True
    assert payload["results"][0]["needs_update"] is False
