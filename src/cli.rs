use std::{collections::HashSet, env, fs, io, path::PathBuf, process::Command};

use clap::{Args, CommandFactory, Parser, Subcommand};
use clap_complete::Shell;

use crate::{
    agent::{render_rules, AgentClient, AgentInstallOptions, AgentScope},
    config::{load_runtime_config, set_aliases, RuntimeConfig},
    error::Result,
    memory::{MemoryUpdateOptions, RememberOptions},
    raw::{RawAddOptions, RawAnalyzeOptions},
    sources::SourceAddOptions,
    util::file_hash,
    vault::{self, BinaryInstallOptions, SetupOptions},
};

const ROOT_ABOUT: &str = "CLI-first local Markdown memory engine for coding agents.";

const ROOT_AFTER_HELP: &str = r#"Command groups:
  Setup and health:
    setup, status, doctor, reindex
  Managed binary and shell integration:
    self install, self update, self shell-init, self completions, uninstall
  Agent rule integration:
    agent rules, agent integrate, agent update, agent status, agent-aliases
  Raw/source capture:
    raw add, raw analyze, raw list, raw inspect, raw mark-processed, source add, lookup-source
  Wiki:
    wiki status, wiki read, wiki search, wiki ingest, wiki synthesize, wiki lint
  Memories and review:
    remember, memory update, review list, review approve, review reject
  Retrieval:
    search, probe, context, inspect, open
  Session capture:
    session finalize

Home resolution:
  Memora uses MEMORA_HOME when set, otherwise ~/memora.
  The public CLI intentionally has no --home flag; use MEMORA_HOME for tests or custom homes.

Common values:
  --mode: auto, text, vector, hybrid
  --intent: auto, memory, wiki, evidence, mixed
  --client: all, agents, cursor, claude, codex
  --scope: project, user

Run `memora help <command>` or `memora help <command> <subcommand>` for command-specific arguments.
See docs/cli-agent-reference.md for the full command reference."#;

#[derive(Debug, Parser)]
#[command(name = "memora")]
#[command(
    version,
    about = ROOT_ABOUT,
    long_about = ROOT_ABOUT,
    after_help = ROOT_AFTER_HELP
)]
pub struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    #[command(about = "Create or repair the managed Memora home directory.")]
    Setup(SetupCommand),
    #[command(about = "Print resolved Memora paths and current configuration status.")]
    Status,
    #[command(about = "Validate memories, raw files, sources, and wiki pages.")]
    Doctor,
    #[command(about = "Rebuild the local SQLite search index.")]
    Reindex(ReindexCommand),
    #[command(name = "self")]
    #[command(about = "Install, update, or initialize the managed Memora binary.")]
    SelfCommand {
        #[command(subcommand)]
        command: SelfCommands,
    },
    #[command(about = "Remove generated state and the managed binary; keep the vault by default.")]
    Uninstall(UninstallCommand),
    #[command(about = "Generate, install, update, and inspect agent instruction files.")]
    Agent {
        #[command(subcommand)]
        command: AgentCommands,
    },
    #[command(name = "agent-aliases")]
    #[command(about = "List or update Remi trigger aliases used in generated agent rules.")]
    AgentAliases {
        #[command(subcommand)]
        command: AgentAliasCommands,
    },
    #[command(about = "Stage and inspect raw material before source capture.")]
    Raw {
        #[command(subcommand)]
        command: RawCommands,
    },
    #[command(about = "Create curated source records from raw material or extracted notes.")]
    Source {
        #[command(subcommand)]
        command: SourceCommands,
    },
    #[command(name = "lookup-source")]
    #[command(about = "Read a curated source by source id.")]
    LookupSource(LookupSourceCommand),
    #[command(about = "Read, search, maintain, and synthesize wiki pages.")]
    Wiki {
        #[command(subcommand)]
        command: WikiCommands,
    },
    #[command(about = "Create one atomic memory.")]
    Remember(RememberCommand),
    #[command(about = "Update existing memory metadata or body text.")]
    Memory {
        #[command(subcommand)]
        command: MemoryCommands,
    },
    #[command(about = "Review pending memories and approve or reject them.")]
    Review {
        #[command(subcommand)]
        command: ReviewCommands,
    },
    #[command(about = "Search memories with text, vector, or hybrid retrieval.")]
    Search(SearchCommand),
    #[command(about = "Agent discovery across memories and wiki with compact routing output.")]
    Probe(ProbeCommand),
    #[command(about = "Build a compact task context across memories, wiki, and sources.")]
    Context(ContextCommand),
    #[command(about = "Show one memory by id.")]
    Inspect(InspectCommand),
    #[command(about = "Print or launch the file path for one memory id.")]
    Open(OpenCommand),
    #[command(about = "Finalize session summaries and proposed memories.")]
    Session {
        #[command(subcommand)]
        command: SessionCommands,
    },
}

#[derive(Debug, Args)]
struct SetupCommand {
    #[arg(
        long,
        help = "Print planned paths without creating directories or config."
    )]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct ReindexCommand {
    #[arg(long, help = "Delete the existing index before rebuilding it.")]
    clean: bool,
}

#[derive(Debug, Subcommand)]
enum SelfCommands {
    #[command(about = "Copy a binary into MEMORA_HOME/bin and install shell integration.")]
    Install(SelfInstallCommand),
    #[command(about = "Overwrite the managed binary and repair shell integration.")]
    Update(SelfUpdateCommand),
    #[command(about = "Print shell completions for a supported shell.")]
    Completions(SelfCompletionsCommand),
    #[command(name = "shell-init")]
    #[command(
        about = "Print shell commands that export Memora env vars, PATH, cache dir, and alias."
    )]
    ShellInit(SelfShellInitCommand),
}

#[derive(Debug, Args)]
struct SelfInstallCommand {
    #[arg(
        long,
        value_name = "PATH",
        help = "Binary to install; defaults to the current executable."
    )]
    from: Option<PathBuf>,
    #[arg(
        long,
        value_name = "SHA256",
        help = "Expected SHA-256 for --from; accepts raw hex or sha256:<hex>."
    )]
    sha256: Option<String>,
    #[arg(long, help = "Overwrite MEMORA_HOME/bin/memora if it already exists.")]
    force: bool,
    #[arg(long, help = "Print planned actions without writing files.")]
    dry_run: bool,
    #[arg(long, help = "Do not create or repair shell startup integration.")]
    no_shell_integration: bool,
}

#[derive(Debug, Args)]
struct SelfUpdateCommand {
    #[arg(
        long,
        value_name = "PATH",
        help = "Local binary to install; when omitted, download from GitHub Releases."
    )]
    from: Option<PathBuf>,
    #[arg(
        long,
        value_name = "SHA256",
        help = "Expected SHA-256 for --from; accepts raw hex or sha256:<hex>."
    )]
    sha256: Option<String>,
    #[arg(
        long,
        env = "MEMORA_REPO",
        default_value = "arxanter/memora",
        value_name = "OWNER/REPO",
        help = "GitHub repository to download from when --from is omitted."
    )]
    repo: String,
    #[arg(
        long,
        env = "MEMORA_VERSION",
        default_value = "latest",
        value_name = "TAG",
        help = "GitHub release tag to download when --from is omitted; use latest for the latest release."
    )]
    version: String,
    #[arg(long, help = "Print planned actions without writing files.")]
    dry_run: bool,
    #[arg(long, help = "Do not create or repair shell startup integration.")]
    no_shell_integration: bool,
}

#[derive(Debug, Args)]
struct SelfCompletionsCommand {
    #[arg(
        value_enum,
        value_name = "SHELL",
        help = "Shell to generate completions for."
    )]
    shell: Shell,
}

#[derive(Debug, Args)]
struct SelfShellInitCommand {
    #[arg(value_enum, value_name = "SHELL", help = "Shell syntax to print.")]
    shell: Shell,
}

#[derive(Debug, Args)]
struct UninstallCommand {
    #[arg(
        long,
        help = "Also remove the vault; without this flag, vault/ and config.yaml are preserved."
    )]
    remove_vault: bool,
    #[arg(long, help = "Print removal targets without deleting them.")]
    dry_run: bool,
}

#[derive(Debug, Subcommand)]
enum AgentCommands {
    #[command(about = "Print generated agent rules without writing files.")]
    Rules(AgentRulesCommand),
    #[command(about = "Install generated rules into client-specific instruction files.")]
    Integrate(AgentInstallCommand),
    #[command(about = "Refresh already installed managed rule blocks.")]
    Update(AgentInstallCommand),
    #[command(about = "Report whether generated rule blocks are installed and current.")]
    Status(AgentStatusCommand),
}

#[derive(Debug, Args)]
struct AgentRulesCommand {
    #[arg(long, value_enum, default_value_t = AgentClient::All, help = "Agent client to render rules for.")]
    client: AgentClient,
    #[arg(long, value_enum, default_value_t = AgentScope::Project, help = "Rule scope to describe in generated instructions.")]
    scope: AgentScope,
}

#[derive(Debug, Args)]
struct AgentInstallCommand {
    #[arg(long, value_enum, default_value_t = AgentClient::All, help = "Client to install rules for.")]
    client: AgentClient,
    #[arg(long, value_enum, default_value_t = AgentScope::Project, help = "Install into project-level or user-level rule locations.")]
    scope: AgentScope,
    #[arg(
        long,
        value_name = "DIR",
        help = "Project directory used for project-scoped targets."
    )]
    project: Option<PathBuf>,
    #[arg(
        long,
        value_name = "PATH",
        help = "Explicit output file; overrides default client target resolution."
    )]
    target: Option<PathBuf>,
    #[arg(long, help = "Print planned writes without changing files.")]
    dry_run: bool,
    #[arg(
        long,
        help = "Append a fresh block if an existing managed block is partial or malformed."
    )]
    force: bool,
}

#[derive(Debug, Args)]
struct AgentStatusCommand {
    #[arg(long, value_enum, default_value_t = AgentClient::All, help = "Client to inspect.")]
    client: AgentClient,
    #[arg(long, value_enum, default_value_t = AgentScope::Project, help = "Rule scope to inspect.")]
    scope: AgentScope,
    #[arg(
        long,
        value_name = "DIR",
        help = "Project directory used for project-scoped target resolution."
    )]
    project: Option<PathBuf>,
}

#[derive(Debug, Subcommand)]
enum AgentAliasCommands {
    #[command(about = "Print configured aliases, one per line.")]
    List,
    #[command(about = "Replace the configured alias list.")]
    Set {
        #[arg(
            value_name = "NAME",
            help = "Alias to use as an explicit memory trigger."
        )]
        names: Vec<String>,
    },
}

#[derive(Debug, Subcommand)]
enum RawCommands {
    #[command(about = "Copy raw material into the managed raw inbox.")]
    Add(RawAddCommand),
    #[command(about = "Prepare an extract draft and risk scan for raw material.")]
    Analyze(RawAnalyzeCommand),
    #[command(about = "List raw files under the raw area or a supplied path.")]
    List {
        #[arg(value_name = "PATH", help = "Optional raw directory or file to list.")]
        path: Option<PathBuf>,
    },
    #[command(about = "Print one raw file and its sidecar metadata.")]
    Inspect {
        #[arg(value_name = "PATH", help = "Raw path to inspect.")]
        path: PathBuf,
    },
    #[command(name = "mark-processed")]
    #[command(about = "Move a raw file to raw/processed and optionally link it to a source.")]
    MarkProcessed(RawMarkProcessedCommand),
}

#[derive(Debug, Args)]
struct RawAddCommand {
    #[arg(value_name = "PATH", help = "Local file to stage as raw material.")]
    path: PathBuf,
    #[arg(
        long,
        value_name = "KIND",
        help = "Source kind, for example text, meeting, article, transcript."
    )]
    kind: String,
    #[arg(
        long,
        value_name = "FORMAT",
        help = "Input format, for example markdown, text, json."
    )]
    format: String,
    #[arg(
        long,
        value_name = "TITLE",
        help = "Human-readable title; defaults to the file name."
    )]
    title: Option<String>,
    #[arg(
        long,
        value_name = "LEVEL",
        help = "Sensitivity label; defaults to public."
    )]
    sensitivity: Option<String>,
    #[arg(
        long = "tag",
        value_name = "TAG",
        help = "Tag to attach; repeat for multiple tags."
    )]
    tags: Vec<String>,
    #[arg(
        long,
        help = "Print planned destination and metadata without writing files."
    )]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct RawAnalyzeCommand {
    #[arg(value_name = "PATH", help = "Raw file to analyze.")]
    path: PathBuf,
    #[arg(
        long,
        value_name = "PATH",
        help = "Write the extract draft to this path instead of the default analysis path."
    )]
    output: Option<PathBuf>,
    #[arg(long, help = "Overwrite an existing extract draft.")]
    overwrite: bool,
    #[arg(long, help = "Print planned analysis output without writing files.")]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct RawMarkProcessedCommand {
    #[arg(value_name = "PATH", help = "Raw file to move into raw/processed.")]
    path: PathBuf,
    #[arg(
        long,
        value_name = "SOURCE_ID",
        help = "Curated source id created from this raw material."
    )]
    source_id: Option<String>,
    #[arg(long, help = "Print planned move without changing files.")]
    dry_run: bool,
}

#[derive(Debug, Subcommand)]
enum SourceCommands {
    #[command(about = "Add a curated source record and optional extract.")]
    Add(SourceAddCommand),
}

#[derive(Debug, Args)]
struct SourceAddCommand {
    #[arg(
        value_name = "PATH",
        help = "Source file or raw file to preserve as curated evidence."
    )]
    path: PathBuf,
    #[arg(
        long,
        value_name = "PATH",
        help = "Concise extract file to store alongside the source."
    )]
    extract: Option<PathBuf>,
    #[arg(
        long,
        value_name = "KIND",
        help = "Source kind; inferred when omitted."
    )]
    kind: Option<String>,
    #[arg(
        long,
        value_name = "FORMAT",
        help = "Source format; inferred when omitted."
    )]
    format: Option<String>,
    #[arg(long, value_name = "TITLE", help = "Human-readable source title.")]
    title: Option<String>,
    #[arg(
        long,
        value_name = "URL",
        help = "Original source URL, when available."
    )]
    url: Option<String>,
    #[arg(
        long,
        value_name = "LEVEL",
        help = "Sensitivity label; inferred from raw metadata when available."
    )]
    sensitivity: Option<String>,
    #[arg(
        long = "tag",
        value_name = "TAG",
        help = "Tag to attach; repeat for multiple tags."
    )]
    tags: Vec<String>,
}

#[derive(Debug, Args)]
struct LookupSourceCommand {
    #[arg(value_name = "SOURCE_ID", help = "Source id to read.")]
    source_id: String,
    #[arg(
        long,
        value_name = "TEXT",
        help = "Optional query printed with the result for agent context."
    )]
    query: Option<String>,
    #[arg(
        long,
        default_value_t = 800,
        help = "Approximate character budget for returned source content."
    )]
    budget: usize,
}

#[derive(Debug, Subcommand)]
enum WikiCommands {
    #[command(about = "Print wiki page counts and storage paths.")]
    Status,
    #[command(about = "Read a wiki page by page key or path.")]
    Read(WikiReadCommand),
    #[command(about = "Search wiki pages.")]
    Search(WikiSearchCommand),
    #[command(about = "Ingest a curated source into wiki entity/concept pages.")]
    Ingest(WikiIngestCommand),
    #[command(about = "Synthesize a saved or ephemeral wiki answer from existing knowledge.")]
    Synthesize(WikiSynthesizeCommand),
    #[command(about = "Validate wiki page frontmatter and links.")]
    Lint,
}

#[derive(Debug, Args)]
struct WikiReadCommand {
    #[arg(
        value_name = "TARGET",
        help = "Wiki page key, title, or relative path."
    )]
    target: String,
    #[arg(long, help = "Return the full page instead of a compact excerpt.")]
    full: bool,
    #[arg(long, value_name = "N", help = "Maximum characters to print.")]
    max_chars: Option<usize>,
}

#[derive(Debug, Args)]
struct WikiSearchCommand {
    #[arg(value_name = "QUERY", help = "Wiki search query.")]
    query: String,
    #[arg(long, value_name = "N", help = "Maximum number of wiki results.")]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct WikiIngestCommand {
    #[arg(value_name = "SOURCE_ID", help = "Curated source id to ingest.")]
    source_id: String,
    #[arg(long, value_name = "TITLE", help = "Title for a source wiki page.")]
    title: Option<String>,
    #[arg(
        long = "entity",
        value_name = "NAME",
        help = "Entity page to update; repeat for multiple entities."
    )]
    entities: Vec<String>,
    #[arg(
        long = "concept",
        value_name = "NAME",
        help = "Concept page to update; repeat for multiple concepts."
    )]
    concepts: Vec<String>,
}

#[derive(Debug, Args)]
struct WikiSynthesizeCommand {
    #[arg(
        value_name = "QUESTION",
        help = "Question to answer from saved knowledge."
    )]
    question: String,
    #[arg(
        long,
        value_name = "TITLE",
        help = "Title to use when --save is passed."
    )]
    title: Option<String>,
    #[arg(long, help = "Save the synthesis as a wiki page.")]
    save: bool,
    #[arg(long, value_name = "N", help = "Maximum number of candidates to use.")]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct RememberCommand {
    #[arg(
        long = "type",
        value_name = "TYPE",
        help = "Memory type: fact, preference, decision, context, task, or conversation."
    )]
    memory_type: String,
    #[arg(long, value_name = "TEXT", help = "Atomic memory body.")]
    text: String,
    #[arg(
        long,
        value_name = "SCOPE",
        help = "Memory scope, for example project or user."
    )]
    scope: Option<String>,
    #[arg(
        long,
        value_name = "PROJECT",
        help = "Project key to attach, for example memory-project."
    )]
    project: Option<String>,
    #[arg(
        long,
        value_name = "STATUS",
        help = "Review status, for example pending or active."
    )]
    status: Option<String>,
    #[arg(
        long = "tag",
        value_name = "TAG",
        help = "Tag to attach; repeat for multiple tags."
    )]
    tags: Vec<String>,
}

#[derive(Debug, Subcommand)]
enum MemoryCommands {
    #[command(about = "Update one memory's metadata or body text.")]
    Update(MemoryUpdateCommand),
}

#[derive(Debug, Args)]
struct MemoryUpdateCommand {
    #[arg(value_name = "MEMORY_ID", help = "Memory id to update.")]
    memory_id: String,
    #[arg(long = "type", value_name = "TYPE", help = "Replace memory type.")]
    memory_type: Option<String>,
    #[arg(long, value_name = "SCOPE", help = "Replace memory scope.")]
    scope: Option<String>,
    #[arg(long, value_name = "PROJECT", help = "Replace project key.")]
    project: Option<String>,
    #[arg(long, help = "Remove the project key.")]
    clear_project: bool,
    #[arg(long, value_name = "STATUS", help = "Replace review status.")]
    status: Option<String>,
    #[arg(long, value_name = "FLOAT", help = "Replace confidence score.")]
    confidence: Option<f32>,
    #[arg(long, help = "Remove confidence score.")]
    clear_confidence: bool,
    #[arg(
        long = "tag",
        value_name = "TAG",
        help = "Replace tags with the repeated --tag values."
    )]
    tags: Vec<String>,
    #[arg(long, help = "Remove all tags.")]
    clear_tags: bool,
    #[arg(long, value_name = "TITLE", help = "Replace title.")]
    title: Option<String>,
    #[arg(long, help = "Remove title.")]
    clear_title: bool,
    #[arg(long, value_name = "TEXT", help = "Replace memory body text.")]
    text: Option<String>,
    #[arg(
        long,
        value_name = "TEXT",
        help = "Reason to append to update history."
    )]
    reason: Option<String>,
    #[arg(long, help = "Print the updated memory without writing it.")]
    dry_run: bool,
}

#[derive(Debug, Subcommand)]
enum ReviewCommands {
    #[command(about = "List pending review items.")]
    List {
        #[arg(
            long,
            value_name = "FIELD",
            help = "Optional grouping field, for example type or source."
        )]
        group_by: Option<String>,
    },
    #[command(about = "Approve pending memories.")]
    Approve(ReviewDecisionCommand),
    #[command(about = "Reject pending memories.")]
    Reject(ReviewDecisionCommand),
}

#[derive(Debug, Args)]
struct ReviewDecisionCommand {
    #[arg(
        value_name = "ID",
        help = "Memory id to approve or reject; repeat for multiple ids."
    )]
    ids: Vec<String>,
    #[arg(
        long,
        value_name = "TEXT",
        help = "Reason to record in review history."
    )]
    reason: Option<String>,
    #[arg(long, help = "Print decisions without writing changes.")]
    dry_run: bool,
}

#[derive(Debug, Args)]
struct SearchCommand {
    #[arg(value_name = "QUERY", help = "Search query.")]
    query: String,
    #[arg(
        long = "variant",
        value_name = "QUERY",
        help = "Additional query variant; repeat for synonyms/translations."
    )]
    variants: Vec<String>,
    #[arg(
        long,
        value_name = "PROJECT",
        help = "Restrict results to a project key."
    )]
    project: Option<String>,
    #[arg(long = "type", value_name = "TYPE", help = "Restrict memory type.")]
    memory_type: Option<String>,
    #[arg(long, value_name = "STATUS", help = "Restrict review status.")]
    status: Option<String>,
    #[arg(long, value_name = "SCOPE", help = "Restrict memory scope.")]
    scope: Option<String>,
    #[arg(long, value_name = "N", help = "Maximum number of results.")]
    limit: Option<usize>,
    #[arg(
        long,
        default_value = "auto",
        value_name = "MODE",
        help = "Retrieval mode: auto, text, vector, or hybrid."
    )]
    mode: String,
    #[arg(long, help = "Expand direct matches through indexed memory relations.")]
    include_related: bool,
}

#[derive(Debug, Args)]
struct ProbeCommand {
    #[arg(value_name = "QUERY", help = "Agent discovery query.")]
    query: String,
    #[arg(
        long = "variant",
        value_name = "QUERY",
        help = "Additional query variant; repeat for synonyms/translations."
    )]
    variants: Vec<String>,
    #[arg(
        long,
        value_name = "PROJECT",
        help = "Restrict memory results to a project key."
    )]
    project: Option<String>,
    #[arg(
        long,
        default_value = "auto",
        value_name = "INTENT",
        help = "Routing intent: auto, memory, wiki, evidence, or mixed."
    )]
    intent: String,
    #[arg(
        long,
        help = "Reserved for future loaded output; current output stays compact."
    )]
    load: bool,
    #[arg(
        long,
        default_value = "auto",
        value_name = "MODE",
        help = "Memory retrieval mode: auto, text, vector, or hybrid."
    )]
    mode: String,
    #[arg(long, help = "Expand memory matches through indexed relations.")]
    include_related: bool,
}

#[derive(Debug, Args)]
struct ContextCommand {
    #[arg(value_name = "QUERY", help = "Task or question to build context for.")]
    query: String,
    #[arg(
        long = "variant",
        value_name = "QUERY",
        help = "Additional query variant; repeat for synonyms/translations."
    )]
    variants: Vec<String>,
    #[arg(
        long,
        value_name = "PROJECT",
        help = "Restrict memory results to a project key."
    )]
    project: Option<String>,
    #[arg(
        long,
        default_value = "auto",
        value_name = "INTENT",
        help = "Routing intent: auto, memory, wiki, evidence, or mixed."
    )]
    intent: String,
    #[arg(
        long,
        default_value_t = 1200,
        value_name = "CHARS",
        help = "Approximate character budget for packed context."
    )]
    budget: usize,
    #[arg(
        long,
        help = "Reserved for future loaded output; current output stays packed."
    )]
    load: bool,
    #[arg(
        long,
        default_value = "auto",
        value_name = "MODE",
        help = "Memory retrieval mode: auto, text, vector, or hybrid."
    )]
    mode: String,
    #[arg(long, help = "Expand memory matches through indexed relations.")]
    include_related: bool,
}

#[derive(Debug, Args)]
struct InspectCommand {
    #[arg(value_name = "MEMORY_ID", help = "Memory id to inspect.")]
    id: String,
}

#[derive(Debug, Args)]
struct OpenCommand {
    #[arg(
        value_name = "MEMORY_ID",
        help = "Memory id whose path should be printed."
    )]
    id: String,
    #[arg(long, help = "Open the memory file with the platform file opener.")]
    launch: bool,
}

#[derive(Debug, Subcommand)]
enum SessionCommands {
    #[command(about = "Capture a completed session source and proposed pending memories.")]
    Finalize(SessionFinalizeCommand),
}

#[derive(Debug, Args)]
struct SessionFinalizeCommand {
    #[arg(
        value_name = "TRANSCRIPT",
        help = "Optional transcript file to preserve as a source."
    )]
    transcript: Option<PathBuf>,
    #[arg(long, value_name = "PATH", help = "Required session summary file.")]
    summary_file: PathBuf,
    #[arg(
        long,
        value_name = "PATH",
        help = "Optional JSON file containing proposed memories."
    )]
    memories_file: Option<PathBuf>,
    #[arg(
        long,
        value_name = "PROJECT",
        help = "Project key for proposed memories and source metadata."
    )]
    project: Option<String>,
    #[arg(
        long = "tag",
        value_name = "TAG",
        help = "Tag to attach; repeat for multiple tags."
    )]
    tags: Vec<String>,
    #[arg(
        long,
        help = "Validate inputs and print planned actions without writing files."
    )]
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
            let config = load_runtime_config()?;
            for (key, value) in vault::status(&config) {
                println!("{key}: {value}");
            }
            Ok(())
        }
        Commands::Agent { command } => dispatch_agent(command),
        Commands::AgentAliases { command } => dispatch_agent_aliases(command),
        Commands::Doctor => {
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
            let stats = crate::indexer::reindex(&config, command.clean)?;
            println!("indexed: {}", config.index_path().display());
            println!("documents_seen: {}", stats.documents_seen);
            println!("documents_indexed: {}", stats.documents_indexed);
            println!("documents_skipped: {}", stats.documents_skipped);
            println!("documents_removed: {}", stats.documents_removed);
            println!("chunks_indexed: {}", stats.chunks_indexed);
            Ok(())
        }
        Commands::SelfCommand { command } => dispatch_self(command),
        Commands::Uninstall(command) => {
            let config = load_runtime_config()?;
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
        Commands::Raw { command } => dispatch_raw(command),
        Commands::Source { command } => dispatch_source(command),
        Commands::LookupSource(command) => {
            let config = load_runtime_config()?;
            let text = crate::sources::lookup_source(&config, &command.source_id, command.budget)?;
            if let Some(query) = command.query {
                println!("query: {query}");
            }
            println!("{text}");
            Ok(())
        }
        Commands::Wiki { command } => dispatch_wiki(command),
        Commands::Remember(command) => {
            let config = load_runtime_config()?;
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
        Commands::Memory { command } => dispatch_memory(command),
        Commands::Review { command } => dispatch_review(command),
        Commands::Search(command) => {
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
            let memory = crate::memory::inspect(&config, &command.id)?;
            println!("{}", memory.path.display());
            if command.launch {
                open_path(&memory.path)?;
            }
            Ok(())
        }
        Commands::Session { command } => {
            let config = load_runtime_config()?;
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

fn dispatch_agent(command: AgentCommands) -> Result<()> {
    match command {
        AgentCommands::Rules(command) => {
            let config = load_runtime_config()?;
            print!("{}", render_rules(&config, command.client, command.scope));
            Ok(())
        }
        AgentCommands::Integrate(command) => {
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
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
            let config = load_runtime_config()?;
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

fn dispatch_self(command: SelfCommands) -> Result<()> {
    match command {
        SelfCommands::Install(command) => {
            let config = if command.dry_run {
                load_runtime_config()?
            } else {
                vault::setup_home(SetupOptions { dry_run: false })?
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
            print_shell_integration_status(maybe_install_shell_integration(
                &config,
                command.no_shell_integration,
                command.dry_run,
            )?);
            println!(
                "next: eval \"$(MEMORA_HOME={} {} self shell-init zsh)\"",
                shell_quote(&config.home_path.display().to_string()),
                shell_quote(&target.display().to_string())
            );
            Ok(())
        }
        SelfCommands::Update(command) => {
            let config = load_runtime_config()?;
            let release_plan = github_release_plan(&command.repo, &command.version)?;
            if command.from.is_none() && command.dry_run {
                println!("download: {}", release_plan.asset_url);
                println!("checksums: {}", release_plan.checksums_url);
                let target = vault::bin_path(&config);
                println!("updated: {}", target.display());
                println!("vault_preserved: true");
                println!("dry_run: true");
                print_shell_integration_status(maybe_install_shell_integration(
                    &config,
                    command.no_shell_integration,
                    true,
                )?);
                return Ok(());
            }
            let mut downloaded_update = None;
            let (source, expected_sha256) = if let Some(source) = command.from {
                (Some(source), command.sha256)
            } else {
                let download = download_github_update(&config, release_plan, command.sha256)?;
                println!("downloaded: {}", download.asset_url);
                println!("checksum: {}", download.expected_sha256);
                let source = download.binary_path.clone();
                let expected = Some(download.expected_sha256.clone());
                downloaded_update = Some(download);
                (Some(source), expected)
            };
            let target = vault::install_binary(
                &config,
                BinaryInstallOptions {
                    source,
                    expected_sha256,
                    overwrite: true,
                    dry_run: command.dry_run,
                },
            )?;
            if let Some(download) = &downloaded_update {
                let _ = fs::remove_file(&download.binary_path);
                let _ = fs::remove_file(&download.checksums_path);
            }
            println!("updated: {}", target.display());
            println!("vault_preserved: true");
            if command.dry_run {
                println!("dry_run: true");
            }
            print_shell_integration_status(maybe_install_shell_integration(
                &config,
                command.no_shell_integration,
                command.dry_run,
            )?);
            Ok(())
        }
        SelfCommands::Completions(command) => {
            let mut cli = Cli::command();
            clap_complete::generate(command.shell, &mut cli, "memora", &mut io::stdout());
            Ok(())
        }
        SelfCommands::ShellInit(command) => {
            let config = load_runtime_config()?;
            print_shell_init(&config, command.shell);
            Ok(())
        }
    }
}

fn dispatch_agent_aliases(command: AgentAliasCommands) -> Result<()> {
    match command {
        AgentAliasCommands::List => {
            let config = load_runtime_config()?;
            for alias in config.file.agent_policy.aliases {
                println!("{alias}");
            }
            Ok(())
        }
        AgentAliasCommands::Set { names } => {
            let mut config = load_runtime_config()?;
            set_aliases(&mut config, names)?;
            println!("aliases: {}", config.file.agent_policy.aliases.join(", "));
            println!("next: run `memora agent update` to refresh installed agent rules");
            Ok(())
        }
    }
}

fn dispatch_raw(command: RawCommands) -> Result<()> {
    let config = load_runtime_config()?;
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

fn dispatch_source(command: SourceCommands) -> Result<()> {
    let config = load_runtime_config()?;
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

fn dispatch_wiki(command: WikiCommands) -> Result<()> {
    let config = load_runtime_config()?;
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

fn dispatch_memory(command: MemoryCommands) -> Result<()> {
    let config = load_runtime_config()?;
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

fn dispatch_review(command: ReviewCommands) -> Result<()> {
    let config = load_runtime_config()?;
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

struct GithubReleasePlan {
    asset: String,
    asset_url: String,
    checksums_url: String,
}

struct DownloadedUpdate {
    asset_url: String,
    binary_path: PathBuf,
    checksums_path: PathBuf,
    expected_sha256: String,
}

fn github_release_plan(repo: &str, version: &str) -> Result<GithubReleasePlan> {
    let repo = repo.trim();
    if repo.is_empty() || !repo.contains('/') {
        return Err(crate::error::MemoraError::InvalidArgument(
            "--repo must be OWNER/REPO".to_string(),
        ));
    }
    let version = version.trim();
    if version.is_empty() {
        return Err(crate::error::MemoraError::InvalidArgument(
            "--version must not be empty".to_string(),
        ));
    }

    let asset = format!("memora-{}", release_target()?);
    let base = if version == "latest" {
        format!("https://github.com/{repo}/releases/latest/download")
    } else {
        format!("https://github.com/{repo}/releases/download/{version}")
    };
    Ok(GithubReleasePlan {
        asset: asset.clone(),
        asset_url: format!("{base}/{asset}"),
        checksums_url: format!("{base}/SHA256SUMS"),
    })
}

fn release_target() -> Result<&'static str> {
    match (env::consts::OS, env::consts::ARCH) {
        ("macos", "aarch64") => Ok("aarch64-apple-darwin"),
        ("macos", "x86_64") => Ok("x86_64-apple-darwin"),
        ("linux", "x86_64") => Ok("x86_64-unknown-linux-gnu"),
        (os, arch) => Err(crate::error::MemoraError::InvalidArgument(format!(
            "unsupported update platform: {os} {arch}"
        ))),
    }
}

fn download_github_update(
    config: &RuntimeConfig,
    plan: GithubReleasePlan,
    expected_override: Option<String>,
) -> Result<DownloadedUpdate> {
    let download_dir = config.state_path().join("cache").join("updates");
    fs::create_dir_all(&download_dir)?;
    let suffix = std::process::id();
    let binary_path = download_dir.join(format!("{}.{}", plan.asset, suffix));
    let checksums_path = download_dir.join(format!("SHA256SUMS.{suffix}"));

    curl_download(&plan.asset_url, &binary_path)?;
    curl_download(&plan.checksums_url, &checksums_path)?;

    let expected_sha256 = if let Some(expected) = expected_override {
        normalize_sha256_for_cli(&expected)
    } else {
        checksum_for_asset(&checksums_path, &plan.asset)?
    };
    let actual_sha256 = file_hash(&binary_path)?;
    if actual_sha256 != expected_sha256 {
        let _ = fs::remove_file(&binary_path);
        let _ = fs::remove_file(&checksums_path);
        return Err(crate::error::MemoraError::InvalidArgument(format!(
            "sha256 mismatch for {}: expected {}, got {}",
            plan.asset, expected_sha256, actual_sha256
        )));
    }

    Ok(DownloadedUpdate {
        asset_url: plan.asset_url,
        binary_path,
        checksums_path,
        expected_sha256,
    })
}

fn curl_download(url: &str, output: &std::path::Path) -> Result<()> {
    let result = Command::new("curl")
        .args(["-fsSL", url, "-o"])
        .arg(output)
        .output();
    let output_result = match result {
        Ok(output_result) => output_result,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Err(crate::error::MemoraError::NotFound(
                "curl is required for `memora self update` downloads".to_string(),
            ));
        }
        Err(error) => return Err(error.into()),
    };
    if !output_result.status.success() {
        let stderr = String::from_utf8_lossy(&output_result.stderr)
            .trim()
            .to_string();
        return Err(crate::error::MemoraError::Message(format!(
            "download failed for {url}: {stderr}"
        )));
    }
    Ok(())
}

fn checksum_for_asset(path: &std::path::Path, asset: &str) -> Result<String> {
    let raw = fs::read_to_string(path)?;
    for line in raw.lines() {
        let mut parts = line.split_whitespace();
        let Some(checksum) = parts.next() else {
            continue;
        };
        let Some(name) = parts.next() else {
            continue;
        };
        if name == asset || name == format!("*{asset}") {
            return Ok(normalize_sha256_for_cli(checksum));
        }
    }
    Err(crate::error::MemoraError::NotFound(format!(
        "checksum for {asset} in {}",
        path.display()
    )))
}

fn normalize_sha256_for_cli(value: &str) -> String {
    value
        .trim()
        .trim_start_matches("sha256:")
        .trim()
        .to_ascii_lowercase()
}

fn print_shell_init(config: &RuntimeConfig, shell: Shell) {
    let home = shell_quote(&config.home_path.display().to_string());
    let bin = shell_quote(&config.home_path.join("bin").display().to_string());
    let fastembed_cache = shell_quote(
        &config
            .state_path()
            .join("cache")
            .join("fastembed")
            .display()
            .to_string(),
    );
    match shell {
        Shell::Fish => {
            println!("set -gx MEMORA_HOME {home};");
            println!("set -gx FASTEMBED_CACHE_DIR {fastembed_cache};");
            println!("fish_add_path {bin};");
            println!("alias memora {bin};");
        }
        Shell::PowerShell => {
            let home = ps_quote(&config.home_path.display().to_string());
            let bin = ps_quote(&config.home_path.join("bin").display().to_string());
            let fastembed_cache = ps_quote(
                &config
                    .state_path()
                    .join("cache")
                    .join("fastembed")
                    .display()
                    .to_string(),
            );
            println!("$env:MEMORA_HOME = {home}");
            println!("$env:FASTEMBED_CACHE_DIR = {fastembed_cache}");
            println!("$env:PATH = {bin} + [IO.Path]::PathSeparator + $env:PATH");
            println!("Set-Alias -Name memora -Value {bin}");
        }
        _ => {
            println!("export MEMORA_HOME={home}");
            println!("export FASTEMBED_CACHE_DIR={fastembed_cache}");
            println!("export PATH={bin}:$PATH");
            println!("alias memora={bin}");
        }
    }
}

struct ShellIntegrationStatus {
    shell: Shell,
    path: Option<PathBuf>,
    changed: bool,
    dry_run: bool,
    skipped: bool,
}

const SHELL_INTEGRATION_START: &str = "# >>> memora shell integration >>>";
const SHELL_INTEGRATION_END: &str = "# <<< memora shell integration <<<";

fn maybe_install_shell_integration(
    config: &RuntimeConfig,
    no_shell_integration: bool,
    dry_run: bool,
) -> Result<ShellIntegrationStatus> {
    let shell = detect_current_shell();
    if no_shell_integration || shell_integration_disabled_by_env() {
        return Ok(ShellIntegrationStatus {
            shell,
            path: None,
            changed: false,
            dry_run,
            skipped: true,
        });
    }

    let Some(path) = shell_startup_file(shell)? else {
        return Ok(ShellIntegrationStatus {
            shell,
            path: None,
            changed: false,
            dry_run,
            skipped: false,
        });
    };

    let block = render_shell_integration_block(config, shell);
    let current = fs::read_to_string(&path).unwrap_or_default();
    let next = upsert_shell_integration_block(&current, &block);
    let changed = current != next;
    if changed && !dry_run {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&path, next)?;
    }

    Ok(ShellIntegrationStatus {
        shell,
        path: Some(path),
        changed,
        dry_run,
        skipped: false,
    })
}

fn print_shell_integration_status(status: ShellIntegrationStatus) {
    if status.skipped {
        println!("shell_integration: skipped");
        return;
    }
    let Some(path) = status.path else {
        println!("shell_integration: unsupported shell={:?}", status.shell);
        return;
    };
    let state = match (status.changed, status.dry_run) {
        (true, true) => "would_install",
        (true, false) => "installed",
        (false, _) => "current",
    };
    println!("shell_integration: {state} path={}", path.display());
}

fn detect_current_shell() -> Shell {
    let shell_name = env::var_os("SHELL")
        .and_then(|value| PathBuf::from(value).file_name().map(|name| name.to_owned()))
        .and_then(|name| name.to_str().map(str::to_string))
        .unwrap_or_else(|| "zsh".to_string());
    match shell_name.as_str() {
        "bash" => Shell::Bash,
        "fish" => Shell::Fish,
        "pwsh" | "powershell" => Shell::PowerShell,
        "elvish" => Shell::Elvish,
        _ => Shell::Zsh,
    }
}

fn shell_startup_file(shell: Shell) -> Result<Option<PathBuf>> {
    let home = PathBuf::from(env::var_os("HOME").ok_or(crate::error::MemoraError::HomeNotFound)?);
    Ok(match shell {
        Shell::Bash => Some(home.join(".bashrc")),
        Shell::Fish => Some(home.join(".config").join("fish").join("config.fish")),
        Shell::Zsh => Some(home.join(".zshrc")),
        _ => None,
    })
}

fn render_shell_integration_block(config: &RuntimeConfig, shell: Shell) -> String {
    let home = sh_single_quote(&config.home_path.display().to_string());
    let bin = sh_single_quote(
        &config
            .home_path
            .join("bin")
            .join("memora")
            .display()
            .to_string(),
    );
    let command = match shell {
        Shell::Fish => format!("env MEMORA_HOME={home} {bin} self shell-init fish | source"),
        Shell::Bash => format!("eval \"$(MEMORA_HOME={home} {bin} self shell-init bash)\""),
        _ => format!("eval \"$(MEMORA_HOME={home} {bin} self shell-init zsh)\""),
    };
    format!(
        "{SHELL_INTEGRATION_START}\n# Managed by Memora. Re-run `memora self install` or `memora self update` to update.\n{command}\n{SHELL_INTEGRATION_END}\n"
    )
}

fn upsert_shell_integration_block(current: &str, block: &str) -> String {
    if let (Some(start), Some(end)) = (
        current.find(SHELL_INTEGRATION_START),
        current.find(SHELL_INTEGRATION_END),
    ) {
        if end >= start {
            let end_index = end + SHELL_INTEGRATION_END.len();
            let mut next = String::new();
            next.push_str(current[..start].trim_end());
            if !next.is_empty() {
                next.push_str("\n\n");
            }
            next.push_str(block.trim_end());
            let suffix = current[end_index..].trim_start();
            if !suffix.is_empty() {
                next.push_str("\n\n");
                next.push_str(suffix);
            }
            next.push('\n');
            return next;
        }
    }

    let mut next = current.trim_end().to_string();
    if !next.is_empty() {
        next.push_str("\n\n");
    }
    next.push_str(block.trim_end());
    next.push('\n');
    next
}

fn shell_integration_disabled_by_env() -> bool {
    env::var("MEMORA_SHELL_INTEGRATION")
        .map(|value| matches!(value.trim(), "0" | "false" | "False" | "no" | "No"))
        .unwrap_or(false)
}

fn shell_quote(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn sh_single_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
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
