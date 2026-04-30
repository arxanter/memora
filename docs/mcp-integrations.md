# MCP Integrations

Stage 3 adds a minimal MCP server skeleton for coding-agent clients. The server
uses the same config loader, schema validation, and Markdown write path as the
CLI. Retrieval-oriented tools are stable placeholders until indexing and recall
land in later stages.

## Install

Install the project with the optional MCP dependency:

```bash
pip install -e ".[mcp]"
```

Initialize a vault if needed:

```bash
memory init ~/MemoryVault --json
```

Point agent clients at the vault with `AGENT_MEMORY_VAULT`:

```bash
export AGENT_MEMORY_VAULT=~/MemoryVault
```

## Tool Policy

Available tools:

- `remember(memory)`
- `search(query, filters)`
- `recall(query, budget, filters)`
- `brief(query, budget, filters)`
- `inspect(id)`
- `explain_recall(query, budget, filters)`
- `mark_status(id, status)`

`remember` is the only Stage 3 tool that writes data. Agent-authored memories
default to the config's `agent_default_status`, which is `pending` in the
generated config. They include `author.kind: agent` and require source and
confidence metadata through the shared schema validator. The server supplies
safe defaults when an agent omits source or confidence.

Retrieval and lifecycle tools return structured JSON placeholders with
`implemented: false` and `citations: []`. `mark_status` does not mutate files in
Stage 3.

## Codex

Add an MCP server entry to the Codex MCP configuration and pass the vault path as
an environment variable:

```toml
[mcp_servers.agent-memory]
command = "memory-mcp"
env = { AGENT_MEMORY_VAULT = "/Users/you/MemoryVault" }
```

If `memory-mcp` is not on `PATH`, use the Python module form:

```toml
[mcp_servers.agent-memory]
command = "python"
args = ["-m", "agent_memory.mcp_server"]
env = { AGENT_MEMORY_VAULT = "/Users/you/MemoryVault" }
```

## Claude Code

Register the server as a stdio MCP process:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "memory-mcp",
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

Claude Code can then call `remember` with a memory object such as:

```json
{
  "type": "decision",
  "text": "Use Markdown as the durable memory source.",
  "scope": "project",
  "project": "agent-memory",
  "confidence": 0.82,
  "source": {
    "path": "Sources/2026-04-30_mcp/source.md",
    "title": "MCP setup notes"
  },
  "tags": ["mcp", "memory"]
}
```

## Cursor

Add an MCP server entry in Cursor settings:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "memory-mcp",
      "env": {
        "AGENT_MEMORY_VAULT": "/Users/you/MemoryVault"
      }
    }
  }
}
```

Restart or reload Cursor after changing MCP settings so the server process is
discovered.

