use std::{collections::HashSet, io, path::PathBuf};

use clap::{Args, CommandFactory, Parser, Subcommand};
use clap_complete::Shell;

use crate::{
    agent::{render_rules, AgentClient, AgentInstallOptions, AgentScope},
    config::{load_runtime_config, set_aliases, RuntimeConfig},
    error::Result,
    memory::{MemoryUpdateOptions, RememberOptions},
    raw::{RawAddOptions, RawAnalyzeOptions},
    sources::SourceAddOptions,
    vault::{self, BinaryInstallOptions, SetupOptions},
};

#[derive(Debug, Parser)]
#[command(name = "memora")]
#[command(
    version,
    about = "CLI-first local Markdown memory engine for coding agents."
)]
pub struct Cli {
    #[arg(long, env = "MEMORA_HOME", global = true)]
    home: Option<PathBuf>,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    Setup(SetupCommand),
    Status,
    Doctor,
    Reindex(ReindexCommand),
    #[command(name = "self")]
    SelfCommand {
        #[command(subcommand)]
        command: SelfCommands,
    },
    Uninstall(UninstallCommand),
    Agent {
        #[command(subcommand)]
        command: AgentCommands,
    },
    #[command(name = "agent-aliases")]
    AgentAliases {
        #[command(subcommand)]
        command: AgentAliasCommands,
    },
    Raw {
        #[command(subcommand)]
        command: RawCommands,
    },
    Source {
        #[command(subcommand)]
        command: SourceCommands,
    },
    #[command(name = "lookup-source")]
    LookupSource(LookupSourceCommand),
    Wiki {
        #[command(subcommand)]
        command: WikiCommands,
    },
    Remember(RememberCommand),
    Memory {
        #[command(subcommand)]
        command: MemoryCommands,
    },
    Review {
        #[command(subcommand)]
        command: ReviewCommands,
    },
    Search(SearchCommand),
    Probe(ProbeCommand),
    Context(ContextCommand),
    Inspect(InspectCommand),
    Open(OpenCommand),
    Session {
        #[command(subcommand)]
        command: SessionCommands,
    },
}

#[derive(Debug, Args)]
struct SetupCommand {
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct ReindexCommand {
    #[arg(long)]
    clean: bool,
}

#[derive(Debug, Subcommand)]
enum SelfCommands {
    Install(SelfInstallCommand),
    Update(SelfUpdateCommand),
    Completions(SelfCompletionsCommand),
    #[command(name = "shell-init")]
    ShellInit(SelfShellInitCommand),
}

#[derive(Debug, Args)]
struct SelfInstallCommand {
    #[arg(long)]
    from: Option<PathBuf>,
    #[arg(long)]
    sha256: Option<String>,
    #[arg(long)]
    force: bool,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct SelfUpdateCommand {
    #[arg(long)]
    from: Option<PathBuf>,
    #[arg(long)]
    sha256: Option<String>,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct SelfCompletionsCommand {
    #[arg(value_enum)]
    shell: Shell,
}

#[derive(Debug, Args)]
struct SelfShellInitCommand {
    #[arg(value_enum)]
    shell: Shell,
}

#[derive(Debug, Args)]
struct UninstallCommand {
    #[arg(long)]
    remove_vault: bool,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Subcommand)]
enum AgentCommands {
    Rules(AgentRulesCommand),
    Integrate(AgentInstallCommand),
    Update(AgentInstallCommand),
    Status(AgentStatusCommand),
}

#[derive(Debug, Args)]
struct AgentRulesCommand {
    #[arg(long, value_enum, default_value_t = AgentClient::All)]
    client: AgentClient,
    #[arg(long, value_enum, default_value_t = AgentScope::Project)]
    scope: AgentScope,
}

#[derive(Debug, Args)]
struct AgentInstallCommand {
    #[arg(long, value_enum, default_value_t = AgentClient::All)]
    client: AgentClient,
    #[arg(long, value_enum, default_value_t = AgentScope::Project)]
    scope: AgentScope,
    #[arg(long)]
    project: Option<PathBuf>,
    #[arg(long)]
    target: Option<PathBuf>,
    #[arg(long)]
    dry_run: bool,
    #[arg(long)]
    force: bool,
}

#[derive(Debug, Args)]
struct AgentStatusCommand {
    #[arg(long, value_enum, default_value_t = AgentClient::All)]
    client: AgentClient,
    #[arg(long, value_enum, default_value_t = AgentScope::Project)]
    scope: AgentScope,
    #[arg(long)]
    project: Option<PathBuf>,
}

#[derive(Debug, Subcommand)]
enum AgentAliasCommands {
    List,
    Set { names: Vec<String> },
}

#[derive(Debug, Subcommand)]
enum RawCommands {
    Add(RawAddCommand),
    Analyze(RawAnalyzeCommand),
    List {
        path: Option<PathBuf>,
    },
    Inspect {
        path: PathBuf,
    },
    #[command(name = "mark-processed")]
    MarkProcessed(RawMarkProcessedCommand),
}

#[derive(Debug, Args)]
struct RawAddCommand {
    path: PathBuf,
    #[arg(long)]
    kind: String,
    #[arg(long)]
    format: String,
    #[arg(long)]
    title: Option<String>,
    #[arg(long)]
    sensitivity: Option<String>,
    #[arg(long = "tag")]
    tags: Vec<String>,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct RawAnalyzeCommand {
    path: PathBuf,
    #[arg(long)]
    output: Option<PathBuf>,
    #[arg(long)]
    overwrite: bool,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct RawMarkProcessedCommand {
    path: PathBuf,
    #[arg(long)]
    source_id: Option<String>,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Subcommand)]
enum SourceCommands {
    Add(SourceAddCommand),
}

#[derive(Debug, Args)]
struct SourceAddCommand {
    path: PathBuf,
    #[arg(long)]
    extract: Option<PathBuf>,
    #[arg(long)]
    kind: Option<String>,
    #[arg(long)]
    format: Option<String>,
    #[arg(long)]
    title: Option<String>,
    #[arg(long)]
    url: Option<String>,
    #[arg(long)]
    sensitivity: Option<String>,
    #[arg(long = "tag")]
    tags: Vec<String>,
}

#[derive(Debug, Args)]
struct LookupSourceCommand {
    source_id: String,
    #[arg(long)]
    query: Option<String>,
    #[arg(long, default_value_t = 800)]
    budget: usize,
}

#[derive(Debug, Subcommand)]
enum WikiCommands {
    Status,
    Read(WikiReadCommand),
    Search(SearchCommand),
    Ingest(WikiIngestCommand),
    Synthesize(WikiSynthesizeCommand),
    Lint,
}

#[derive(Debug, Args)]
struct WikiReadCommand {
    target: String,
    #[arg(long)]
    full: bool,
    #[arg(long)]
    max_chars: Option<usize>,
}

#[derive(Debug, Args)]
struct WikiIngestCommand {
    source_id: String,
    #[arg(long)]
    title: Option<String>,
    #[arg(long = "entity")]
    entities: Vec<String>,
    #[arg(long = "concept")]
    concepts: Vec<String>,
}

#[derive(Debug, Args)]
struct WikiSynthesizeCommand {
    question: String,
    #[arg(long)]
    title: Option<String>,
    #[arg(long)]
    save: bool,
    #[arg(long)]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct RememberCommand {
    #[arg(long = "type")]
    memory_type: String,
    #[arg(long)]
    text: String,
    #[arg(long)]
    scope: Option<String>,
    #[arg(long)]
    project: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long = "tag")]
    tags: Vec<String>,
}

#[derive(Debug, Subcommand)]
enum MemoryCommands {
    Update(MemoryUpdateCommand),
}

#[derive(Debug, Args)]
struct MemoryUpdateCommand {
    memory_id: String,
    #[arg(long = "type")]
    memory_type: Option<String>,
    #[arg(long)]
    scope: Option<String>,
    #[arg(long)]
    project: Option<String>,
    #[arg(long)]
    clear_project: bool,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    confidence: Option<f32>,
    #[arg(long)]
    clear_confidence: bool,
    #[arg(long = "tag")]
    tags: Vec<String>,
    #[arg(long)]
    clear_tags: bool,
    #[arg(long)]
    title: Option<String>,
    #[arg(long)]
    clear_title: bool,
    #[arg(long)]
    text: Option<String>,
    #[arg(long)]
    reason: Option<String>,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Subcommand)]
enum ReviewCommands {
    List {
        #[arg(long)]
        group_by: Option<String>,
    },
    Approve(ReviewDecisionCommand),
    Reject(ReviewDecisionCommand),
}

#[derive(Debug, Args)]
struct ReviewDecisionCommand {
    ids: Vec<String>,
    #[arg(long)]
    reason: Option<String>,
    #[arg(long)]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct SearchCommand {
    query: String,
    #[arg(long = "variant")]
    variants: Vec<String>,
    #[arg(long)]
    project: Option<String>,
    #[arg(long = "type")]
    memory_type: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    scope: Option<String>,
    #[arg(long)]
    limit: Option<usize>,
    #[arg(long, default_value = "auto")]
    mode: String,
    #[arg(long)]
    include_related: bool,
}

#[derive(Debug, Args)]
struct ProbeCommand {
    query: String,
    #[arg(long = "variant")]
    variants: Vec<String>,
    #[arg(long)]
    project: Option<String>,
    #[arg(long, default_value = "auto")]
    intent: String,
    #[arg(long)]
    load: bool,
    #[arg(long, default_value = "auto")]
    mode: String,
    #[arg(long)]
    include_related: bool,
}

#[derive(Debug, Args)]
struct ContextCommand {
    query: String,
    #[arg(long = "variant")]
    variants: Vec<String>,
    #[arg(long)]
    project: Option<String>,
    #[arg(long, default_value = "auto")]
    intent: String,
    #[arg(long, default_value_t = 1200)]
    budget: usize,
    #[arg(long)]
    load: bool,
    #[arg(long, default_value = "auto")]
    mode: String,
    #[arg(long)]
    include_related: bool,
}

#[derive(Debug, Args)]
struct InspectCommand {
    id: String,
}

#[derive(Debug, Args)]
struct OpenCommand {
    id: String,
    #[arg(long)]
    launch: bool,
}

#[derive(Debug, Subcommand)]
enum SessionCommands {
    Finalize(SessionFinalizeCommand),
}

#[derive(Debug, Args)]
struct SessionFinalizeCommand {
    transcript: Option<PathBuf>,
    #[arg(long)]
    summary_file: PathBuf,
    #[arg(long)]
    memories_file: Option<PathBuf>,
    #[arg(long)]
    project: Option<String>,
    #[arg(long = "tag")]
    tags: Vec<String>,
    #[arg(long)]
    dry_run: bool,
}

pub fn run() -> Result<()> {
    let cli = Cli::parse();
    dispatch(cli)
}

fn dispatch(cli: Cli) -> Result<()> {
    match cli.command {
        Commands::Setup(command) => {
            let config = vault::setup_home(SetupOptions {
                home: cli.home,
                dry_run: command.dry_run,
            })?;
            println!("home: {}", config.home_path.display());
            println!("vault: {}", config.vault_path.display());
            if command.dry_run {
                println!("dry_run: true");
            } else {
                println!("setup complete");
            }
            Ok(())
        }
        Commands::Status => {
            let config = load_runtime_config(cli.home)?;
            for (key, value) in vault::status(&config) {
                println!("{key}: {value}");
            }
            Ok(())
        }
        Commands::Agent { command } => dispatch_agent(cli.home, command),
        Commands::AgentAliases { command } => dispatch_agent_aliases(cli.home, command),
        Commands::Doctor => {
            let config = load_runtime_config(cli.home)?;
            let mut issues = crate::memory::validate_all(&config)?;
            issues.extend(crate::raw::validate_all(&config)?);
            issues.extend(crate::sources::validate_all(&config)?);
            issues.extend(crate::wiki::lint(&config)?);
            if issues.is_empty() {
                println!("doctor: ok");
            } else {
                println!("doctor: {} issue(s)", issues.len());
                for issue in issues {
                    println!("- {issue}");
                }
            }
            Ok(())
        }
        Commands::Reindex(command) => {
            let config = load_runtime_config(cli.home)?;
            let stats = crate::indexer::reindex(&config, command.clean)?;
            println!("indexed: {}", config.index_path().display());
            println!("documents_seen: {}", stats.documents_seen);
            println!("documents_indexed: {}", stats.documents_indexed);
            println!("documents_skipped: {}", stats.documents_skipped);
            println!("documents_removed: {}", stats.documents_removed);
            println!("chunks_indexed: {}", stats.chunks_indexed);
            Ok(())
        }
        Commands::SelfCommand { command } => dispatch_self(cli.home, command),
        Commands::Uninstall(command) => {
            let config = load_runtime_config(cli.home)?;
            let targets = vault::uninstall(&config, command.remove_vault, command.dry_run)?;
            println!("vault_preserved: {}", !command.remove_vault);
            if command.dry_run {
                println!("dry_run: true");
            }
            for target in targets {
                println!("removed_target: {}", target.display());
            }
            Ok(())
        }
        Commands::Raw { command } => dispatch_raw(cli.home, command),
        Commands::Source { command } => dispatch_source(cli.home, command),
        Commands::LookupSource(command) => {
            let config = load_runtime_config(cli.home)?;
            let text = crate::sources::lookup_source(&config, &command.source_id, command.budget)?;
            if let Some(query) = command.query {
                println!("query: {query}");
            }
            println!("{text}");
            Ok(())
        }
        Commands::Wiki { command } => dispatch_wiki(cli.home, command),
        Commands::Remember(command) => {
            let config = load_runtime_config(cli.home)?;
            let memory = crate::memory::remember(
                &config,
                RememberOptions {
                    memory_type: command.memory_type,
                    text: command.text,
                    scope: command.scope,
                    project: command.project,
                    status: command.status,
                    tags: command.tags,
                    source: None,
                    author: None,
                    confidence: None,
                },
            )?;
            println!("created: {}", memory.frontmatter.id);
            println!("path: {}", memory.relative_path);
            Ok(())
        }
        Commands::Memory { command } => dispatch_memory(cli.home, command),
        Commands::Review { command } => dispatch_review(cli.home, command),
        Commands::Search(command) => {
            let config = load_runtime_config(cli.home)?;
            let freshness = crate::freshness::refresh_if_needed(&config)?;
            print_freshness(&freshness);
            let queries = planned_queries(&config, command.query, command.variants, "memory");
            if queries.len() > 1 {
                println!("planned_queries: {}", queries.join(" | "));
            }
            let results = merged_search_results(
                &config,
                &queries,
                crate::indexer::SearchFilters {
                    project: command.project,
                    memory_type: command.memory_type,
                    status: command.status,
                    scope: command.scope,
                    limit: command.limit.unwrap_or(10),
                    mode: crate::indexer::SearchMode::parse(&command.mode)?,
                    include_related: command.include_related,
                },
            );
            print_search_results(results);
            Ok(())
        }
        Commands::Probe(command) => {
            let config = load_runtime_config(cli.home)?;
            let freshness = crate::freshness::refresh_if_needed(&config)?;
            print_freshness(&freshness);
            let queries =
                planned_queries(&config, command.query, command.variants, &command.intent);
            let mut found_any = false;
            println!("intent: {}", command.intent);
            println!("load: {}", command.load);
            println!("planned_queries: {}", queries.join(" | "));
            let mode = crate::indexer::SearchMode::parse(&command.mode)?;
            if intent_allows_memory(&command.intent) {
                let results = merged_search_results(
                    &config,
                    &queries,
                    crate::indexer::SearchFilters {
                        project: command.project.clone(),
                        memory_type: None,
                        status: None,
                        scope: None,
                        limit: 5,
                        mode,
                        include_related: command.include_related,
                    },
                );
                if !results.is_empty() {
                    found_any = true;
                    println!("## Memories");
                    print_search_results(results);
                }
            }
            if intent_allows_wiki(&command.intent) {
                let results = merged_wiki_results(&config, &queries, 5)?;
                if !results.is_empty() {
                    found_any = true;
                    println!("## Wiki");
                    print_wiki_results(results);
                }
            }
            println!("has_context: {found_any}");
            println!("memory_needed: {found_any}");
            Ok(())
        }
        Commands::Context(command) => {
            let config = load_runtime_config(cli.home)?;
            let freshness = crate::freshness::refresh_if_needed(&config)?;
            print_freshness(&freshness);
            println!("intent: {}", command.intent);
            println!("budget: {}", command.budget);
            println!("load: {}", command.load);
            let queries =
                planned_queries(&config, command.query, command.variants, &command.intent);
            println!("planned_queries: {}", queries.join(" | "));
            let mode = crate::indexer::SearchMode::parse(&command.mode)?;
            let memory_results = if intent_allows_memory(&command.intent) {
                merged_search_results(
                    &config,
                    &queries,
                    crate::indexer::SearchFilters {
                        project: command.project.clone(),
                        memory_type: None,
                        status: None,
                        scope: None,
                        limit: 5,
                        mode,
                        include_related: command.include_related,
                    },
                )
            } else {
                Vec::new()
            };
            let wiki_results = if intent_allows_wiki(&command.intent) {
                merged_wiki_results(&config, &queries, 5)?
            } else {
                Vec::new()
            };
            let source_results = if intent_allows_sources(&command.intent) {
                merged_source_results(&config, &queries, 5)?
            } else {
                Vec::new()
            };
            print_packed_context(memory_results, wiki_results, source_results, command.budget);
            Ok(())
        }
        Commands::Inspect(command) => {
            let config = load_runtime_config(cli.home)?;
            let memory = crate::memory::inspect(&config, &command.id)?;
            println!("id: {}", memory.frontmatter.id);
            println!("type: {}", memory.frontmatter.memory_type);
            println!("status: {}", memory.frontmatter.status);
            println!("path: {}", memory.relative_path);
            println!();
            println!("{}", memory.body.trim());
            Ok(())
        }
        Commands::Open(command) => {
            let config = load_runtime_config(cli.home)?;
            let memory = crate::memory::inspect(&config, &command.id)?;
            println!("{}", memory.path.display());
            if command.launch {
                open_path(&memory.path)?;
            }
            Ok(())
        }
        Commands::Session { command } => {
            let config = load_runtime_config(cli.home)?;
            match command {
                SessionCommands::Finalize(command) => {
                    let result = crate::session::finalize(
                        &config,
                        crate::session::SessionFinalizeOptions {
                            transcript: command.transcript,
                            summary_file: command.summary_file,
                            memories_file: command.memories_file,
                            project: command.project,
                            tags: command.tags,
                            dry_run: command.dry_run,
                        },
                    )?;
                    if result.dry_run {
                        println!("dry_run: true");
                        return Ok(());
                    }
                    if let Some(source) = result.source {
                        println!("source_id: {}", source.source_id);
                    }
                    println!("pending_memories: {}", result.proposed_memory_ids.len());
                    for id in result.proposed_memory_ids {
                        println!("memory: {id}");
                    }
                    Ok(())
                }
            }
        }
    }
}

fn dispatch_agent(home: Option<PathBuf>, command: AgentCommands) -> Result<()> {
    match command {
        AgentCommands::Rules(command) => {
            let config = load_runtime_config(home)?;
            print!("{}", render_rules(&config, command.client, command.scope));
            Ok(())
        }
        AgentCommands::Integrate(command) => {
            let config = load_runtime_config(home)?;
            let results = crate::agent::integrate_or_update(
                &config,
                AgentInstallOptions {
                    client: command.client,
                    scope: command.scope,
                    project: command.project,
                    target: command.target,
                    dry_run: command.dry_run,
                    force: command.force,
                },
            )?;
            for result in results {
                println!(
                    "{}: client={:?} path={}{}",
                    if result.changed {
                        "updated"
                    } else {
                        "unchanged"
                    },
                    result.client,
                    result.path.display(),
                    if result.dry_run { " (dry-run)" } else { "" }
                );
            }
            Ok(())
        }
        AgentCommands::Update(command) => {
            let config = load_runtime_config(home)?;
            let results = crate::agent::integrate_or_update(
                &config,
                AgentInstallOptions {
                    client: command.client,
                    scope: command.scope,
                    project: command.project,
                    target: command.target,
                    dry_run: command.dry_run,
                    force: command.force,
                },
            )?;
            for result in results {
                println!(
                    "{}: client={:?} path={}{}",
                    if result.changed {
                        "updated"
                    } else {
                        "unchanged"
                    },
                    result.client,
                    result.path.display(),
                    if result.dry_run { " (dry-run)" } else { "" }
                );
            }
            Ok(())
        }
        AgentCommands::Status(command) => {
            let config = load_runtime_config(home)?;
            println!("client: {:?}", command.client);
            println!("scope: {:?}", command.scope);
            if let Some(project) = &command.project {
                println!("project: {}", project.display());
            }
            println!("aliases: {}", config.file.agent_policy.aliases.join(", "));
            println!("enabled: {}", config.file.agent_policy.enabled);
            println!("auto_recall: {}", config.file.agent_policy.auto_recall);
            for entry in crate::agent::status(
                &config,
                command.client,
                command.scope,
                command.project,
                None,
            )? {
                println!(
                    "managed_block: client={:?} installed={} current={} path={}",
                    entry.client,
                    if entry.installed { "true" } else { "false" },
                    if entry.current { "true" } else { "false" },
                    entry.path.display()
                );
            }
            Ok(())
        }
    }
}

fn dispatch_self(home: Option<PathBuf>, command: SelfCommands) -> Result<()> {
    match command {
        SelfCommands::Install(command) => {
            let config = if command.dry_run {
                load_runtime_config(home)?
            } else {
                vault::setup_home(SetupOptions {
                    home,
                    dry_run: false,
                })?
            };
            let target = vault::install_binary(
                &config,
                BinaryInstallOptions {
                    source: command.from,
                    expected_sha256: command.sha256,
                    overwrite: command.force,
                    dry_run: command.dry_run,
                },
            )?;
            println!("installed: {}", target.display());
            println!("vault_preserved: true");
            if command.dry_run {
                println!("dry_run: true");
            }
            println!(
                "next: eval \"$(memora --home {} self shell-init zsh)\"",
                config.home_path.display()
            );
            Ok(())
        }
        SelfCommands::Update(command) => {
            let config = load_runtime_config(home)?;
            let target = vault::install_binary(
                &config,
                BinaryInstallOptions {
                    source: command.from,
                    expected_sha256: command.sha256,
                    overwrite: true,
                    dry_run: command.dry_run,
                },
            )?;
            println!("updated: {}", target.display());
            println!("vault_preserved: true");
            if command.dry_run {
                println!("dry_run: true");
            }
            Ok(())
        }
        SelfCommands::Completions(command) => {
            let mut cli = Cli::command();
            clap_complete::generate(command.shell, &mut cli, "memora", &mut io::stdout());
            Ok(())
        }
        SelfCommands::ShellInit(command) => {
            let config = load_runtime_config(home)?;
            print_shell_init(&config, command.shell);
            Ok(())
        }
    }
}

fn dispatch_agent_aliases(home: Option<PathBuf>, command: AgentAliasCommands) -> Result<()> {
    match command {
        AgentAliasCommands::List => {
            let config = load_runtime_config(home)?;
            for alias in config.file.agent_policy.aliases {
                println!("{alias}");
            }
            Ok(())
        }
        AgentAliasCommands::Set { names } => {
            let mut config = load_runtime_config(home)?;
            set_aliases(&mut config, names)?;
            println!("aliases: {}", config.file.agent_policy.aliases.join(", "));
            println!("next: run `memora agent update` to refresh installed agent rules");
            Ok(())
        }
    }
}

fn dispatch_raw(home: Option<PathBuf>, command: RawCommands) -> Result<()> {
    let config = load_runtime_config(home)?;
    match command {
        RawCommands::Add(command) => {
            let entry = crate::raw::add_raw(
                &config,
                RawAddOptions {
                    path: command.path,
                    kind: command.kind,
                    format: command.format,
                    title: command.title,
                    sensitivity: command.sensitivity,
                    tags: command.tags,
                    dry_run: command.dry_run,
                },
            )?;
            println!("raw: {}", entry.relative_path);
            if let Some(metadata) = entry.metadata {
                println!("raw_id: {}", metadata.raw_id);
                println!("content_hash: {}", metadata.content_hash);
            }
            Ok(())
        }
        RawCommands::Analyze(command) => {
            let analysis = crate::raw::analyze_raw(
                &config,
                RawAnalyzeOptions {
                    path: command.path,
                    output: command.output,
                    overwrite: command.overwrite,
                    dry_run: command.dry_run,
                },
            )?;
            println!("raw: {}", analysis.entry.relative_path);
            println!("analysis: {}", analysis.relative_output_path);
            println!("absolute_path: {}", analysis.output_path.display());
            println!("wrote: {}", analysis.wrote);
            if command.dry_run {
                println!("dry_run: true");
            }
            if analysis.risk_flags.is_empty() {
                println!("risk_flags: none");
            } else {
                println!("risk_flags: {}", analysis.risk_flags.join(", "));
            }
            if command.dry_run {
                println!("{}", analysis.template);
            }
            Ok(())
        }
        RawCommands::List { path } => {
            for entry in crate::raw::list_raw(&config, path)? {
                println!("{}", entry.relative_path);
            }
            Ok(())
        }
        RawCommands::Inspect { path } => {
            let entry = crate::raw::inspect_raw(&config, path)?;
            println!("path: {}", entry.relative_path);
            println!("absolute_path: {}", entry.path.display());
            if let Some(metadata) = entry.metadata {
                println!("{}", serde_yaml::to_string(&metadata)?);
            }
            Ok(())
        }
        RawCommands::MarkProcessed(command) => {
            let entry = crate::raw::mark_processed(
                &config,
                command.path,
                command.source_id,
                command.dry_run,
            )?;
            println!("processed: {}", entry.relative_path);
            Ok(())
        }
    }
}

fn dispatch_source(home: Option<PathBuf>, command: SourceCommands) -> Result<()> {
    let config = load_runtime_config(home)?;
    match command {
        SourceCommands::Add(command) => {
            let source = crate::sources::add_source(
                &config,
                SourceAddOptions {
                    path: command.path,
                    extract: command.extract,
                    kind: command.kind,
                    format: command.format,
                    title: command.title,
                    url: command.url,
                    sensitivity: command.sensitivity,
                    tags: command.tags,
                },
            )?;
            println!("source_id: {}", source.source_id);
            println!("title: {}", source.title);
            println!("source: {}", source.source_path.display());
            if let Some(extract_path) = source.extract_path {
                println!("extract: {}", extract_path.display());
            }
            Ok(())
        }
    }
}

fn dispatch_wiki(home: Option<PathBuf>, command: WikiCommands) -> Result<()> {
    let config = load_runtime_config(home)?;
    match command {
        WikiCommands::Status => {
            let (page_count, issues) = crate::wiki::status(&config)?;
            println!("pages: {page_count}");
            println!("issues: {}", issues.len());
            Ok(())
        }
        WikiCommands::Read(command) => {
            let page = crate::wiki::read_page(&config, &command.target)?;
            println!("path: {}", page.relative_path);
            println!("title: {}", page.frontmatter.title.unwrap_or_default());
            println!();
            let body = page.body.trim();
            if command.full {
                println!("{body}");
            } else {
                let max_chars = command.max_chars.unwrap_or(1200);
                println!("{}", truncate_chars(body, max_chars));
            }
            Ok(())
        }
        WikiCommands::Search(command) => {
            for result in crate::wiki::search(&config, &command.query, command.limit.unwrap_or(10))?
            {
                println!(
                    "{} score={} title={}",
                    result.page.relative_path,
                    result.score,
                    result.page.frontmatter.title.unwrap_or_default()
                );
            }
            Ok(())
        }
        WikiCommands::Ingest(command) => {
            let pages = crate::wiki::ingest_source(
                &config,
                &command.source_id,
                command.title,
                command.entities,
                command.concepts,
            )?;
            for page in pages {
                println!("wrote: {}", page.relative_path);
            }
            Ok(())
        }
        WikiCommands::Synthesize(command) => {
            let body = crate::wiki::synthesize(
                &config,
                &command.question,
                command.title,
                command.save,
                command.limit.unwrap_or(5),
            )?;
            println!("{body}");
            Ok(())
        }
        WikiCommands::Lint => {
            let issues = crate::wiki::lint(&config)?;
            if issues.is_empty() {
                println!("wiki lint: ok");
            } else {
                println!("wiki lint: {} issue(s)", issues.len());
                for issue in issues {
                    println!("- {issue}");
                }
            }
            Ok(())
        }
    }
}

fn dispatch_memory(home: Option<PathBuf>, command: MemoryCommands) -> Result<()> {
    let config = load_runtime_config(home)?;
    match command {
        MemoryCommands::Update(command) => {
            let memory = crate::memory::update_memory(
                &config,
                MemoryUpdateOptions {
                    memory_id: command.memory_id,
                    memory_type: command.memory_type,
                    scope: command.scope,
                    project: command.project,
                    clear_project: command.clear_project,
                    status: command.status,
                    confidence: command.confidence,
                    clear_confidence: command.clear_confidence,
                    tags: command.tags,
                    clear_tags: command.clear_tags,
                    text: command.text,
                },
            )?;
            println!("updated: {}", memory.frontmatter.id);
            println!("path: {}", memory.relative_path);
            if command.dry_run {
                println!("dry_run: ignored for current implementation");
            }
            if let Some(reason) = command.reason {
                println!("reason: {reason}");
            }
            Ok(())
        }
    }
}

fn dispatch_review(home: Option<PathBuf>, command: ReviewCommands) -> Result<()> {
    let config = load_runtime_config(home)?;
    match command {
        ReviewCommands::List { group_by } => {
            if let Some(group_by) = group_by {
                println!("group_by: {group_by}");
            }
            for memory in crate::memory::pending_memories(&config)? {
                println!(
                    "{} type={} path={}",
                    memory.frontmatter.id, memory.frontmatter.memory_type, memory.relative_path
                );
            }
            Ok(())
        }
        ReviewCommands::Approve(command) => {
            let updated = crate::memory::set_review_status(&config, &command.ids, "active")?;
            for memory in updated {
                println!("approved: {}", memory.frontmatter.id);
            }
            if let Some(reason) = command.reason {
                println!("reason: {reason}");
            }
            if command.dry_run {
                println!("dry_run: ignored for current implementation");
            }
            Ok(())
        }
        ReviewCommands::Reject(command) => {
            let updated = crate::memory::set_review_status(&config, &command.ids, "rejected")?;
            for memory in updated {
                println!("rejected: {}", memory.frontmatter.id);
            }
            if let Some(reason) = command.reason {
                println!("reason: {reason}");
            }
            if command.dry_run {
                println!("dry_run: ignored for current implementation");
            }
            Ok(())
        }
    }
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    value
        .chars()
        .take(max_chars.saturating_sub(1))
        .collect::<String>()
        + "..."
}

fn planned_queries(
    config: &RuntimeConfig,
    query: String,
    variants: Vec<String>,
    intent: &str,
) -> Vec<String> {
    let mut planned = Vec::new();
    let inputs = std::iter::once(query).chain(variants);
    for input in inputs {
        push_query_variant(&mut planned, input.trim());
        let without_alias = strip_alias_trigger(config, &input);
        push_query_variant(&mut planned, &without_alias);
        let compact = compact_memory_query(&without_alias);
        push_query_variant(&mut planned, &compact);
    }

    let intent = intent.to_lowercase();
    if intent == "memory" || intent == "auto" || intent == "mixed" {
        let existing = planned.clone();
        for query in existing {
            if likely_decision_query(&query) {
                push_query_variant(&mut planned, &format!("{query} decision"));
            }
            if likely_task_query(&query) {
                push_query_variant(&mut planned, &format!("{query} task todo"));
            }
        }
    }
    planned.truncate(8);
    planned
}

fn push_query_variant(queries: &mut Vec<String>, value: &str) {
    let normalized = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let normalized = normalized.trim_matches(|ch: char| {
        ch.is_whitespace() || matches!(ch, ',' | ':' | ';' | '?' | '!' | '.')
    });
    if normalized.len() < 2 {
        return;
    }
    if !queries
        .iter()
        .any(|existing| existing.eq_ignore_ascii_case(normalized))
    {
        queries.push(normalized.to_string());
    }
}

fn strip_alias_trigger(config: &RuntimeConfig, query: &str) -> String {
    let trimmed = query.trim();
    for alias in &config.file.agent_policy.aliases {
        let alias = alias.trim();
        if alias.is_empty() {
            continue;
        }
        if trimmed.to_lowercase().starts_with(&alias.to_lowercase()) {
            return trimmed[alias.len()..]
                .trim_start_matches(|ch: char| {
                    ch.is_whitespace() || matches!(ch, ',' | ':' | ';' | '-' | '!')
                })
                .to_string();
        }
    }
    trimmed.to_string()
}

fn compact_memory_query(query: &str) -> String {
    let stopwords = [
        "show",
        "tell",
        "about",
        "please",
        "what",
        "did",
        "we",
        "decide",
        "decided",
        "find",
        "memory",
        "memories",
        "context",
        "покажи",
        "покажите",
        "расскажи",
        "найди",
        "что",
        "мы",
        "решили",
        "решение",
        "решения",
        "память",
        "контекст",
        "по",
        "про",
    ];
    query
        .split_whitespace()
        .filter(|token| {
            let normalized = token
                .trim_matches(|ch: char| !ch.is_alphanumeric() && ch != '_')
                .to_lowercase();
            !normalized.is_empty() && !stopwords.contains(&normalized.as_str())
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn likely_decision_query(query: &str) -> bool {
    let lower = query.to_lowercase();
    ["decide", "decided", "decision", "решили", "решение"]
        .iter()
        .any(|needle| lower.contains(needle))
}

fn likely_task_query(query: &str) -> bool {
    let lower = query.to_lowercase();
    ["todo", "task", "plan", "next", "задач", "план", "дальше"]
        .iter()
        .any(|needle| lower.contains(needle))
}

fn intent_allows_memory(intent: &str) -> bool {
    matches!(
        intent.trim().to_lowercase().as_str(),
        "auto" | "mixed" | "memory"
    )
}

fn intent_allows_wiki(intent: &str) -> bool {
    matches!(
        intent.trim().to_lowercase().as_str(),
        "auto" | "mixed" | "wiki"
    )
}

fn intent_allows_sources(intent: &str) -> bool {
    matches!(
        intent.trim().to_lowercase().as_str(),
        "auto" | "mixed" | "evidence" | "source" | "sources"
    )
}

fn merged_search_results(
    config: &RuntimeConfig,
    queries: &[String],
    filters: crate::indexer::SearchFilters,
) -> Vec<crate::indexer::SearchResult> {
    let mut seen = HashSet::new();
    let mut merged = Vec::new();
    for query in queries {
        let limit = filters.limit.max(1);
        let results = crate::indexer::search(config, query, filters.clone()).unwrap_or_default();
        for result in results {
            if seen.insert(result.id.clone()) {
                merged.push(result);
            }
        }
        if merged.len() >= limit {
            break;
        }
    }
    merged.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.id.cmp(&right.id))
    });
    merged.truncate(filters.limit.max(1));
    merged
}

fn merged_wiki_results(
    config: &RuntimeConfig,
    queries: &[String],
    limit: usize,
) -> Result<Vec<crate::wiki::WikiSearchResult>> {
    let mut seen = HashSet::new();
    let mut merged = Vec::new();
    for query in queries {
        for result in crate::wiki::search(config, query, limit)? {
            if seen.insert(result.page.relative_path.clone()) {
                merged.push(result);
            }
        }
        if merged.len() >= limit {
            break;
        }
    }
    merged.truncate(limit.max(1));
    Ok(merged)
}

fn merged_source_results(
    config: &RuntimeConfig,
    queries: &[String],
    limit: usize,
) -> Result<Vec<crate::sources::SourceSearchResult>> {
    let mut seen = HashSet::new();
    let mut merged = Vec::new();
    for query in queries {
        for result in crate::sources::search_sources(config, query, limit)? {
            if seen.insert(result.path.clone()) {
                merged.push(result);
            }
        }
        if merged.len() >= limit {
            break;
        }
    }
    merged.truncate(limit.max(1));
    Ok(merged)
}

fn print_wiki_results(results: Vec<crate::wiki::WikiSearchResult>) {
    if results.is_empty() {
        println!("no results");
        return;
    }
    for result in results {
        println!("{} score={}", result.page.relative_path, result.score);
    }
}

fn print_packed_context(
    memories: Vec<crate::indexer::SearchResult>,
    wiki: Vec<crate::wiki::WikiSearchResult>,
    sources: Vec<crate::sources::SourceSearchResult>,
    budget: usize,
) {
    let mut remaining = budget.max(1);
    let mut used = 0usize;
    println!("## Packed Context");

    println!("### Memories");
    if memories.is_empty() {
        println!("no results");
    } else {
        for result in memories {
            if remaining == 0 {
                break;
            }
            let related = match (&result.related_from, &result.relation) {
                (Some(from), Some(relation)) => format!(" related_to={from} relation={relation}"),
                _ => String::new(),
            };
            let snippet = result.snippet.replace('\n', " ");
            let block = format!(
                "- citation: `{}` id={} type={} status={} score={:.3}{}\n  snippet: {}\n",
                result.path,
                result.id,
                result.memory_type,
                result.status,
                result.score,
                related,
                fallback_snippet(&snippet)
            );
            print_budgeted_block(&block, &mut remaining, &mut used);
        }
    }

    println!("### Wiki");
    if wiki.is_empty() {
        println!("no results");
    } else {
        for result in wiki {
            if remaining == 0 {
                break;
            }
            let title = result
                .page
                .frontmatter
                .title
                .as_deref()
                .unwrap_or("untitled");
            let block = format!(
                "- citation: `{}` title=\"{}\" score={}\n  snippet: {}\n",
                result.page.relative_path,
                title.replace('"', "\\\""),
                result.score,
                fallback_snippet(&result.page.body.replace('\n', " "))
            );
            print_budgeted_block(&block, &mut remaining, &mut used);
        }
    }

    println!("### Sources");
    if sources.is_empty() {
        println!("no results");
    } else {
        for result in sources {
            if remaining == 0 {
                break;
            }
            let block = format!(
                "- citation: `{}` source_id={} score={}\n  snippet: {}\n",
                result.path,
                result.source_id,
                result.score,
                fallback_snippet(&result.snippet)
            );
            print_budgeted_block(&block, &mut remaining, &mut used);
        }
    }
    println!("packed_budget_used: {used}");
    println!("packed_budget_remaining: {remaining}");
}

fn fallback_snippet(snippet: &str) -> String {
    let snippet = snippet.trim();
    if snippet.is_empty() {
        "(no snippet)".to_string()
    } else {
        snippet.to_string()
    }
}

fn print_budgeted_block(block: &str, remaining: &mut usize, used: &mut usize) {
    if *remaining == 0 {
        return;
    }
    let block_len = block.chars().count();
    if block_len <= *remaining {
        print!("{block}");
        *remaining -= block_len;
        *used += block_len;
        return;
    }
    let truncated = truncate_for_budget(block, *remaining);
    let printed = truncated.chars().count();
    println!("{truncated}");
    *used += printed;
    *remaining = 0;
}

fn truncate_for_budget(value: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    let count = value.chars().count();
    if count <= max_chars {
        return value.to_string();
    }
    if max_chars <= 3 {
        return ".".repeat(max_chars);
    }
    value.chars().take(max_chars - 3).collect::<String>() + "..."
}

fn print_search_results(results: Vec<crate::indexer::SearchResult>) {
    if results.is_empty() {
        println!("no results");
        return;
    }
    for result in results {
        let related = match (&result.related_from, &result.relation) {
            (Some(from), Some(relation)) => format!(" related_to={from} relation={relation}"),
            _ => String::new(),
        };
        println!(
            "{} score={:.3} type={} status={} path={}{}",
            result.id, result.score, result.memory_type, result.status, result.path, related
        );
        if !result.snippet.trim().is_empty() {
            println!("  {}", result.snippet.replace('\n', " "));
        }
    }
}

fn print_shell_init(config: &RuntimeConfig, shell: Shell) {
    let home = shell_quote(&config.home_path.display().to_string());
    let bin = shell_quote(&config.home_path.join("bin").display().to_string());
    match shell {
        Shell::Fish => {
            println!("set -gx MEMORA_HOME {home};");
            println!("fish_add_path {bin};");
        }
        Shell::PowerShell => {
            let home = ps_quote(&config.home_path.display().to_string());
            let bin = ps_quote(&config.home_path.join("bin").display().to_string());
            println!("$env:MEMORA_HOME = {home}");
            println!("$env:PATH = {bin} + [IO.Path]::PathSeparator + $env:PATH");
        }
        _ => {
            println!("export MEMORA_HOME={home}");
            println!("export PATH={bin}:$PATH");
        }
    }
}

fn shell_quote(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn ps_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

fn print_freshness(outcome: &crate::freshness::RefreshOutcome) {
    println!(
        "freshness: reason={} checked_files={} reindexed={}",
        outcome.reason, outcome.checked_files, outcome.reindexed
    );
    if let Some(stats) = &outcome.stats {
        println!(
            "freshness_reindex: documents_seen={} documents_indexed={} documents_skipped={}",
            stats.documents_seen, stats.documents_indexed, stats.documents_skipped
        );
    }
}

fn open_path(path: &std::path::Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    let program = "open";
    #[cfg(target_os = "linux")]
    let program = "xdg-open";
    #[cfg(target_os = "windows")]
    let program = "cmd";

    let mut command = std::process::Command::new(program);
    #[cfg(target_os = "windows")]
    command.args(["/C", "start"]).arg(path);
    #[cfg(not(target_os = "windows"))]
    command.arg(path);
    command.status()?;
    Ok(())
}
