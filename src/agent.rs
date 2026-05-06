use std::{env, fs, path::PathBuf};

use clap::ValueEnum;

use crate::config::RuntimeConfig;
use crate::error::{MemoraError, Result};

pub const MANAGED_BLOCK_START: &str = "<!-- BEGIN MEMORA MANAGED BLOCK -->";
pub const MANAGED_BLOCK_END: &str = "<!-- END MEMORA MANAGED BLOCK -->";

pub const AGENT_COMMAND_SPEC: &str = r#"Command Specs:
- Home: commands use `MEMORA_HOME` when set, otherwise `~/.memora`. Do not pass a `--home` flag.
- Repeatable values: pass repeated flags, for example `--variant "alt"` or `--tag "tag"`, instead of comma-separated strings.
- Large write inputs: create temporary `.md`, `.yaml`, `.yml`, `.json`, or other payload files under the project `.memora/` directory or user `~/.memora/temp/`, then pass the file path to Memora. Memora copies/ingests the content and does not delete input files; after a successful command, the agent should remove temporary staged files itself. Small values can be passed directly as CLI arguments.
- Retrieval modes: `auto`, `text`, `vector`, `hybrid`. `auto` tries semantic retrieval when available and falls back to text.
- Retrieval intents: `auto`, `memory`, `wiki`, `evidence`, `mixed`. Use `evidence` when source excerpts are required.
- Agent clients: `all`, `agents`, `cursor`, `claude`, `codex`. Agent scopes: `project`, `user`.
- Memory types: `fact`, `decision`, `preference`, `task`, `project_context`, `conversation_summary`.
- Memory scopes: `user`, `project`, `global`. `project` scope requires a non-empty `project` key.
- Memory statuses: `pending`, `active`, `stale`, `superseded`, `rejected`.
- Raw kinds: `pdf`, `zoom`, `slack`, `text`, `webclip`, `article`.
- Raw formats: `pdf`, `markdown`, `json`, `txt`.
- Sensitivity labels for raw and source records: `normal`, `private`, `secret`.
- Raw statuses: `processed`.
- Source markdown frontmatter `kind`: `source` or `extract`. `source_quality`: `user_provided`, `agent_extracted`, `generated`, `imported`, `unknown`.
- Wiki page types: `source`, `entity`, `concept`, `synthesis`.

Agent-safe Commands:
- Discovery: `memora probe "<query>" --intent <INTENT> --variant "<alternate>" --mode <MODE> --include-related`.
  Output includes `planned_queries`, optional `## Memories` and `## Wiki` sections, and `has_context: true|false`.
- Packed context: `memora context "<query>" --intent <INTENT> --variant "<alternate>" --budget <CHARS> --mode <MODE> --include-related`.
  Output has `## Packed Context`, `### Memories`, `### Wiki`, `### Sources`, citation lines, and `packed_budget_used/remaining`.
- Memory search: `memora search "<query>" --variant "<alternate>" --project <PROJECT> --type <TYPE> --status <STATUS> --scope <SCOPE> --limit <N> --mode <MODE> --include-related`.
  Result lines are `id score=<float> type=<TYPE> status=<STATUS> path=<relative-path>`.
- Source evidence: `memora lookup-source <SOURCE_ID> --query "<query>" --budget <N>`.
- Create memory: `memora remember --type <TYPE> --text "<atomic memory>" --text-file <PATH> --scope <SCOPE> --project <PROJECT> --status <STATUS> --tag <TAG>`.
  Default status is `pending`; default scope is `project` when `--project` is present, otherwise `user`.
- Update memory: `memora memory update <MEMORY_ID> --type <TYPE> --scope <SCOPE> --project <PROJECT> --clear-project --status <STATUS> --confidence <0..1> --clear-confidence --tag <TAG> --clear-tags --title <TITLE> --clear-title --text <TEXT> --text-file <PATH> --reason <TEXT> --dry-run`.
- Review: `memora review list --group-by type|source`, `memora review approve <ID...> --reason <TEXT> --dry-run`, `memora review reject <ID...> --reason <TEXT> --dry-run`.
- Raw capture: `memora raw add <PATH> --kind <KIND> --format <FORMAT> --title <TITLE> --sensitivity <LEVEL> --tag <TAG> --dry-run`.
  For PDFs, this stores the original PDF under `raw/inbox/pdf/` with a `.meta.yaml` sidecar. Create a text or Markdown extract/summary before promoting it to sources, wiki, or memory.
- Raw analysis: `memora raw analyze <RAW_PATH> --output <PATH> --overwrite --dry-run`.
- Source capture: `memora source add <PATH> --extract <PATH> --kind <CHANNEL> --format <FORMAT> --title <TITLE> --url <URL> --sensitivity <LEVEL> --tag <TAG>`.
  `--kind` is stored as source `channel`; common channels are `file`, `ai_session`, `slack`, and `webclip`.
- Raw completion: `memora raw mark-processed <RAW_PATH> --source-id <SOURCE_ID> --dry-run`.
- Wiki: `memora wiki read <TARGET> --full --max-chars <N>`, `memora wiki search "<query>" --limit <N>`, `memora wiki ingest <SOURCE_ID> --title <TITLE> --entity <NAME> --concept <NAME>`, `memora wiki synthesize "<question>" --title <TITLE> --save --limit <N>`, `memora wiki lint`.
- Maintenance: `memora status`, `memora doctor`, `memora reindex --clean`, `memora agent status --client <CLIENT> --scope <SCOPE>`, `memora agent reference`.

Data Formats:
- Memory files are Markdown with YAML frontmatter: `schema_version: 1`, `id`, `type`, `scope`, optional `project`, `status`, optional `confidence` between 0 and 1, `created_at`, `updated_at`, optional `source`, optional `author`, optional `relations`, optional `tags`.
- Agent-generated memories must include `source` and `confidence`; save them as `pending` unless the user explicitly approves active persistence.
- Raw sidecars are `<file>.meta.yaml` with `raw_id`, `kind`, `format`, `title`, `tags`, `sensitivity`, `captured_at`, `original_path`, `file_name`, `size_bytes`, `content_hash`, optional `status`, `processed_at`, `previous_relative_path`, `source_id`.
- Source files are Markdown with YAML frontmatter: `schema_version: 1`, `source_id`, `kind`, `title`, `captured_at`, `channel`, `source_quality`, `sensitivity`, optional `url`, `tags`, `risk_flags`, and `origin`.
- Wiki pages are Markdown with optional YAML frontmatter: `title`, `type`, `source_id`, `sources`, `entities`, `concepts`, `last_updated`.
- `memora session finalize --memories-file <PATH>` expects JSON array items as either strings or objects shaped as `{"type":"decision","text":"...","tags":["tag"]}`.
- Local embedding commands read JSON on stdin shaped as `{"model":"<model>","texts":["..."]}` and return either `{"embeddings":[[0.1,0.2]]}` or a raw `[[0.1,0.2]]` array."#;

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
    pub client: AgentClient,
    pub path: PathBuf,
    pub changed: bool,
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
pub struct AgentStatusEntry {
    pub client: AgentClient,
    pub path: PathBuf,
    pub installed: bool,
    pub current: bool,
}

pub fn render_rules(config: &RuntimeConfig, client: AgentClient, scope: AgentScope) -> String {
    let aliases = config.file.agent_policy.aliases.join(", ");
    let auto_recall = config.file.agent_policy.auto_recall;
    let enabled = config.file.agent_policy.enabled;
    let agent_command_spec = AGENT_COMMAND_SPEC.trim_end();

    let body = format!(
        r#"Memora is the local CLI-first memory vault for this agent.

Client: {client:?}
Scope: {scope:?}
Memory enabled: {enabled}
Auto recall enabled: {auto_recall}
Aliases: {aliases}
Memora home: {}
Command prefix: `memora`

Rules:
- Treat any configured alias as an explicit memory trigger.
- Also use memory without an alias when the request appears to need durable project history, user preferences, previous decisions, roadmap/status/TODOs, Wiki knowledge, or saved source evidence.
- Do not run memory lookup on every turn. Decide whether memory is relevant first.
- Start discovery with `memora probe "<query>" --intent memory|wiki|mixed --variant "<alternate>"`.
- Pass 2-5 high-signal `--variant` values to `probe`, `context`, or `search` when alternate wording, translations, abbreviations, or inflections may improve retrieval.
- Use `memora context "<query>" --intent evidence|mixed --variant "<alternate>"` or `memora lookup-source <source_id>` when source evidence is required.
- Use `memora remember` only for small atomic durable memories, and only after showing the exact proposed memory/extraction/value to the user and receiving approval, unless the user explicitly said review is not required.
- When capturing raw material, preserve it as close to the original as possible. Prefer no text changes; only move the material into a convenient file/format for capture.
- Before any command that saves extractions or values to memory, including `memora raw add`, `memora source add`, `memora raw mark-processed`, `memora wiki ingest`, and `memora wiki synthesize --save`, show what will be saved and get user approval, unless the user explicitly said review is not required.
- Review pending agent-created memory with `memora review`; show active pending notes in a concise, readable format and let the user approve, reject, or edit-and-approve each note before applying the decision.
- Do not read, edit, migrate, delete, or inspect Memora vault internals directly. If the CLI lacks an operation, report the CLI gap.

{agent_command_spec}
"#,
        config.home_path.display()
    );

    format!("{MANAGED_BLOCK_START}\n{body}{MANAGED_BLOCK_END}\n")
}

pub fn integrate_or_update(
    config: &RuntimeConfig,
    options: AgentInstallOptions,
) -> Result<Vec<AgentWriteResult>> {
    let targets = target_specs(
        options.client,
        options.scope,
        options.project,
        options.target,
    )?;
    let mut results = Vec::new();
    for (client, target) in targets {
        let rules = render_rules(config, client, options.scope);
        let mut current = fs::read_to_string(&target).unwrap_or_default();
        if matches!(client, AgentClient::Cursor)
            && target.extension().and_then(|value| value.to_str()) == Some("mdc")
        {
            current = ensure_cursor_mdc_header(&current);
        }
        let next = upsert_managed_block(&current, &rules, options.force)?;
        let changed = current != next;
        if changed && !options.dry_run {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&target, next)?;
        }
        results.push(AgentWriteResult {
            client,
            path: target,
            changed,
            dry_run: options.dry_run,
        });
    }
    Ok(results)
}

pub fn status(
    config: &RuntimeConfig,
    client: AgentClient,
    scope: AgentScope,
    project: Option<PathBuf>,
    target: Option<PathBuf>,
) -> Result<Vec<AgentStatusEntry>> {
    let targets = target_specs(client, scope, project, target)?;
    Ok(targets
        .into_iter()
        .map(|(client, path)| {
            let expected = render_rules(config, client, scope);
            let expected_block = extract_managed_block(&expected).unwrap_or(&expected);
            let content = fs::read_to_string(&path).unwrap_or_default();
            let installed =
                content.contains(MANAGED_BLOCK_START) && content.contains(MANAGED_BLOCK_END);
            let current = installed
                && extract_managed_block(&content)
                    .map(|block| normalize_block(block) == normalize_block(expected_block))
                    .unwrap_or(false);
            AgentStatusEntry {
                client,
                path,
                installed,
                current,
            }
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

fn target_specs(
    client: AgentClient,
    scope: AgentScope,
    project: Option<PathBuf>,
    target: Option<PathBuf>,
) -> Result<Vec<(AgentClient, PathBuf)>> {
    if let Some(target) = target {
        let client = if matches!(client, AgentClient::All) {
            AgentClient::Agents
        } else {
            client
        };
        return Ok(vec![(client, target)]);
    }

    let clients: Vec<AgentClient> = match client {
        AgentClient::All => vec![AgentClient::Cursor, AgentClient::Claude, AgentClient::Codex],
        AgentClient::Agents => vec![AgentClient::Agents],
        other => vec![other],
    };
    clients
        .into_iter()
        .map(|client| Ok((client, target_path(client, scope, project.clone())?)))
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
                AgentClient::Cursor => home.join(".cursor").join("rules").join("memora.mdc"),
                AgentClient::Claude => home.join(".claude").join("CLAUDE.md"),
                AgentClient::Codex => home.join(".codex").join("AGENTS.md"),
                AgentClient::Agents | AgentClient::All => home.join(".memora").join("AGENTS.md"),
            })
        }
    }
}

fn extract_managed_block(content: &str) -> Option<&str> {
    let start = content.find(MANAGED_BLOCK_START)?;
    let end = content.find(MANAGED_BLOCK_END)?;
    if end < start {
        return None;
    }
    Some(&content[start..end + MANAGED_BLOCK_END.len()])
}

fn normalize_block(block: &str) -> String {
    block.trim().replace("\r\n", "\n")
}

fn ensure_cursor_mdc_header(current: &str) -> String {
    if current.trim_start().starts_with("---") {
        return current.to_string();
    }
    let mut next = "---\ndescription: Memora memory rules\nalwaysApply: true\n---\n\n".to_string();
    next.push_str(current.trim_start());
    next
}
