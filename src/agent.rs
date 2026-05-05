use std::{env, fs, path::PathBuf};

use clap::ValueEnum;

use crate::config::RuntimeConfig;
use crate::error::{MemoraError, Result};

pub const MANAGED_BLOCK_START: &str = "<!-- BEGIN MEMORA MANAGED BLOCK -->";
pub const MANAGED_BLOCK_END: &str = "<!-- END MEMORA MANAGED BLOCK -->";

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum AgentClient {
    All,
    Agents,
    Cursor,
    Claude,
    Codex,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum AgentScope {
    Project,
    User,
}

#[derive(Debug, Clone)]
pub struct AgentInstallOptions {
    pub client: AgentClient,
    pub scope: AgentScope,
    pub project: Option<PathBuf>,
    pub target: Option<PathBuf>,
    pub dry_run: bool,
    pub force: bool,
}

#[derive(Debug, Clone)]
pub struct AgentWriteResult {
    pub path: PathBuf,
    pub changed: bool,
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
pub struct AgentStatusEntry {
    pub path: PathBuf,
    pub installed: bool,
}

pub fn render_rules(config: &RuntimeConfig, client: AgentClient, scope: AgentScope) -> String {
    let aliases = config.file.agent_policy.aliases.join(", ");
    let auto_recall = config.file.agent_policy.auto_recall;
    let enabled = config.file.agent_policy.enabled;

    format!(
        r#"{MANAGED_BLOCK_START}
Memora is the local CLI-first memory vault for this agent.

Client: {client:?}
Scope: {scope:?}
Memory enabled: {enabled}
Auto recall enabled: {auto_recall}
Aliases: {aliases}

Rules:
- Treat any configured alias as an explicit memory trigger.
- Also use memory without an alias when the request appears to need durable project history, user preferences, previous decisions, roadmap/status/TODOs, Wiki knowledge, or saved source evidence.
- Do not run memory lookup on every turn. Decide whether memory is relevant first.
- Start discovery with `memora probe "<query>" --intent memory|wiki|mixed --variant "<alternate>"`.
- Use `memora context "<query>" --intent evidence|mixed` or `memora lookup-source <source_id>` when source evidence is required.
- Use `memora remember` only for small atomic durable memories.
- Use `memora raw add`, `memora source add`, `memora raw mark-processed`, `memora wiki ingest`, and `memora wiki synthesize --save` for source capture workflows.
- Review pending agent-created memory with `memora review`; approve or reject only when policy or user confirmation allows it.
- Do not read, edit, migrate, delete, or inspect Memora vault internals directly. If the CLI lacks an operation, report the CLI gap.
{MANAGED_BLOCK_END}
"#
    )
}

pub fn integrate_or_update(
    config: &RuntimeConfig,
    options: AgentInstallOptions,
) -> Result<Vec<AgentWriteResult>> {
    let rules = render_rules(config, options.client, options.scope);
    let targets = target_paths(
        options.client,
        options.scope,
        options.project,
        options.target,
    )?;
    let mut results = Vec::new();
    for target in targets {
        let current = fs::read_to_string(&target).unwrap_or_default();
        let next = upsert_managed_block(&current, &rules, options.force)?;
        let changed = current != next;
        if changed && !options.dry_run {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&target, next)?;
        }
        results.push(AgentWriteResult {
            path: target,
            changed,
            dry_run: options.dry_run,
        });
    }
    Ok(results)
}

pub fn status(
    client: AgentClient,
    scope: AgentScope,
    project: Option<PathBuf>,
    target: Option<PathBuf>,
) -> Result<Vec<AgentStatusEntry>> {
    let targets = target_paths(client, scope, project, target)?;
    Ok(targets
        .into_iter()
        .map(|path| {
            let installed = fs::read_to_string(&path)
                .map(|content| {
                    content.contains(MANAGED_BLOCK_START) && content.contains(MANAGED_BLOCK_END)
                })
                .unwrap_or(false);
            AgentStatusEntry { path, installed }
        })
        .collect())
}

fn upsert_managed_block(current: &str, rules: &str, force: bool) -> Result<String> {
    let start = current.find(MANAGED_BLOCK_START);
    let end = current.find(MANAGED_BLOCK_END);
    match (start, end) {
        (Some(start), Some(end)) if end >= start => {
            let end_index = end + MANAGED_BLOCK_END.len();
            let mut next = String::new();
            next.push_str(current[..start].trim_end());
            if !next.is_empty() {
                next.push_str("\n\n");
            }
            next.push_str(rules.trim_end());
            let suffix = current[end_index..].trim_start();
            if !suffix.is_empty() {
                next.push_str("\n\n");
                next.push_str(suffix);
            }
            next.push('\n');
            Ok(next)
        }
        (None, None) => {
            let mut next = current.trim_end().to_string();
            if !next.is_empty() {
                next.push_str("\n\n");
            }
            next.push_str(rules.trim_end());
            next.push('\n');
            Ok(next)
        }
        _ if force => {
            let mut next = current.trim_end().to_string();
            if !next.is_empty() {
                next.push_str("\n\n");
            }
            next.push_str(rules.trim_end());
            next.push('\n');
            Ok(next)
        }
        _ => Err(MemoraError::InvalidArgument(
            "found a partial Memora managed block; rerun with --force to append a fresh block"
                .to_string(),
        )),
    }
}

fn target_paths(
    client: AgentClient,
    scope: AgentScope,
    project: Option<PathBuf>,
    target: Option<PathBuf>,
) -> Result<Vec<PathBuf>> {
    if let Some(target) = target {
        return Ok(vec![target]);
    }

    let clients: Vec<AgentClient> = match client {
        AgentClient::All => vec![AgentClient::Cursor, AgentClient::Claude, AgentClient::Codex],
        AgentClient::Agents => vec![AgentClient::Agents],
        other => vec![other],
    };
    clients
        .into_iter()
        .map(|client| target_path(client, scope, project.clone()))
        .collect()
}

fn target_path(
    client: AgentClient,
    scope: AgentScope,
    project: Option<PathBuf>,
) -> Result<PathBuf> {
    match scope {
        AgentScope::Project => {
            let root = project.unwrap_or(env::current_dir()?);
            Ok(match client {
                AgentClient::Cursor => root.join(".cursor").join("rules").join("memora.mdc"),
                AgentClient::Claude => root.join("CLAUDE.md"),
                AgentClient::Codex | AgentClient::Agents | AgentClient::All => {
                    root.join("AGENTS.md")
                }
            })
        }
        AgentScope::User => {
            let home = PathBuf::from(env::var_os("HOME").ok_or(MemoraError::HomeNotFound)?);
            Ok(match client {
                AgentClient::Cursor => home.join(".memora").join("cursor-user-rules.md"),
                AgentClient::Claude => home.join(".claude").join("CLAUDE.md"),
                AgentClient::Codex => home.join(".codex").join("AGENTS.md"),
                AgentClient::Agents | AgentClient::All => home.join(".memora").join("AGENTS.md"),
            })
        }
    }
}
