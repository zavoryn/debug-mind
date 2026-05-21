**English** | [中文](README.md)

<p align="center">
  <img src="docs/logo.svg" alt="DebugMind" width="120" height="120" />
  <h1 align="center">DebugMind</h1>
  <p align="center">
    <strong>AI-Powered Bug Diagnosis Agent with Experiential Memory</strong><br/>
    <em>The more bugs it sees, the faster it gets.</em>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
  <img src="https://img.shields.io/badge/MCP-compatible-purple" alt="MCP Compatible" />
  <img src="https://img.shields.io/badge/RAG-powered-orange" alt="RAG Powered" />
  <a href="https://github.com/zavoryn/debug-mind/actions/workflows/test.yml"><img src="https://github.com/zavoryn/debug-mind/actions/workflows/test.yml/badge.svg?branch=master" alt="tests" /></a>
  <a href="https://github.com/zavoryn/debug-mind/actions/workflows/lint.yml"><img src="https://github.com/zavoryn/debug-mind/actions/workflows/lint.yml/badge.svg?branch=master" alt="lint" /></a>
  <img src="https://img.shields.io/badge/pypi-pre--release-lightgrey" alt="PyPI pre-release" />
</p>

---

## The Problem

Every debugging session starts from scratch. You hit a bug, Google it, read StackOverflow, dig through logs — even if someone on your team solved the exact same issue last week.

**What if your debugging tool remembered every bug it ever diagnosed?**

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                     Bug Report                          │
│           "NPE on login, Redis errors in log"           │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │  Memory Search │  ◄── Vector similarity (ChromaDB)
              │  "Seen this?"  │      + keyword match
              └───────┬───────┘
                      │
          ┌───────────┼───────────┐
          ▼                       ▼
   [Similar Case Found]    [No Match]
          │                       │
   Load past diagnosis     Full AI diagnosis
   + Fast-track fix        + Systematic RCA
          │                       │
          └───────────┬───────────┘
                      ▼
              ┌───────────────┐
              │ Diagnosis + Fix│
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │  Save to Memory│  ──► Markdown file (git-friendly)
              │  for next time │  ──► Vector embedding (searchable)
              └───────────────┘
```

## Architecture

DebugMind has **four layers**, each independently useful:

| Layer | Component | What It Does |
|-------|-----------|-------------|
| **Memory** | ChromaDB + Markdown | Hybrid storage — vector search + human-readable files |
| **Skills** | ripgrep / grep | Real code search, file reading, project structure |
| **Agent** | Claude + ReAct Loop | Tool-use driven diagnostic reasoning |
| **Protocol** | MCP Server | Expose memory to any MCP-compatible client |
| **Interface** | CLI (Rich) | Interactive terminal with colored output |

### Why This Architecture?

- **ChromaDB** is embedded — zero infrastructure, works locally
- **Markdown files** are git-friendly — your team can share a bug knowledge repo
- **MCP protocol** makes the memory accessible from Claude Code, Claude Desktop, or any MCP client
- **Agent loop** is the standard ReAct pattern (Reason + Act) with tool use

## Quick Start

```bash
# Install
pip install -e .

# Optional: OpenAI compatible provider
pip install -e ".[openai]"
DEBUG_MIND_PROVIDER=openai OPENAI_API_KEY=your-key debug-mind diagnose "..."

# Optional: custom embedding models
pip install -e ".[embeddings]"
DEBUG_MIND_EMBEDDING=openai debug-mind rebuild

# Create .env with your API key
echo "ANTHROPIC_API_KEY=your-key-here" > .env

# Diagnose a bug with codebase access (the real power)
debug-mind diagnose --project /path/to/your/codebase \
  --log error.log \
  --env "java=17,framework=Spring Boot 3.2" \
  "NullPointerException on UserService.login during peak hours"

# Or diagnose without a codebase (memory-only mode)
debug-mind diagnose "Service returns 500 intermittently"

# Search past cases
debug-mind search "redis connection timeout"

# Browse memory
debug-mind list
debug-mind stats

# View or delete a specific case
debug-mind show <case_id>
debug-mind delete <case_id>

# Start MCP server (for Claude Code / Desktop integration)
debug-mind serve
```

## MCP Integration

DebugMind exposes its memory as an **MCP Server**, so any MCP-compatible client can use it:

```json
// In your MCP client config (e.g., Claude Desktop's claude_desktop_config.json)
{
  "mcpServers": {
    "debug-mind": {
      "command": "python",
      "args": ["-m", "debug_mind.tools.mcp_server"]
    }
  }
}
```

This gives Claude (or any MCP client) these tools:
- `search_similar_bugs` — search past bug cases
- `save_bug_case` — save new diagnosis to memory
- `list_recent_bugs` — browse recent cases
- `get_bug_stats` — see memory statistics
- `delete_bug_case` — remove a case from memory

## Memory Format

Every bug case is saved as a Markdown file in `memory/cases/`:

```markdown
# NPE in UserService.login when Redis pool exhausted

> case_id: `abc123` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2

## Symptoms
Login returns 500, NullPointerException at line 42

## Root Cause
Redis connection pool exhausted → getLoginToken() returns null → NPE

## Fix Suggestion
1. Increase pool size to 32
2. Add null check before .equals()

## Tags
npe, redis, spring-boot, connection-pool
```

These files are:
- **Version-controllable** — commit them to a shared repo
- **Human-readable** — browse them in any Markdown viewer
- **Rebuildable** — `debug-mind rebuild` re-indexes all files into ChromaDB

## Use Cases

### Personal Debugging Assistant
Keep a local memory of every bug you diagnose. Next time you hit something similar, DebugMind finds it in seconds.

### Team Knowledge Base
Share the `memory/` directory via git. Everyone's bug diagnoses contribute to a shared knowledge pool.

### CI/CD Integration
Feed build failures into DebugMind. If a test fails with an error you've seen before, it tells you immediately.

### Interview Conversation Starter
> "I built an AI debugging agent with a RAG-powered memory system. It uses vector similarity search to match new bugs against past diagnoses, wrapped in an MCP server so any AI client can access the knowledge base."

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| LLM | Claude (Anthropic API) | Best-in-class tool use and reasoning |
| Agent Framework | Custom ReAct loop | Lightweight, no heavy dependencies |
| Vector DB | ChromaDB | Embedded, zero-config, fast |
| Persistence | Markdown files | Git-friendly, human-readable |
| Protocol | MCP (Model Context Protocol) | Standard for AI tool integration |
| CLI | Click + Rich | Beautiful terminal output |
| Schema | Pydantic v2 | Type-safe data contracts |

## Project Structure

```
debug-mind/
├── src/debug_mind/
│   ├── schemas.py          # Pydantic data models
│   ├── agent.py            # Core diagnostic agent (ReAct loop + tool use)
│   ├── cli.py              # CLI interface (Click + Rich)
│   ├── memory/
│   │   └── store.py        # Hybrid memory (ChromaDB + Markdown)
│   ├── skills/
│   │   └── codebase.py     # Real code search (ripgrep/grep) + file reading
│   └── tools/
│       └── mcp_server.py   # MCP server for external clients
├── memory/
│   └── examples/           # example bug cases (Markdown)
├── tests/
│   ├── test_memory_store.py  # Memory store + codebase skill tests
│   ├── test_agent.py         # Agent tool dispatch tests
│   ├── test_cli.py           # CLI command tests
│   └── test_schemas.py       # Schema validation tests
├── docs/
│   └── logo.svg              # Project logo
└── pyproject.toml
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, code style, and how to add bug cases or skills.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/
```

## Roadmap

- [ ] Multi-project support (separate memory namespaces)
- [ ] Web UI for browsing and searching the knowledge base
- [ ] Community bug knowledge repo (shared embeddings)
- [ ] Support for OpenAI / local LLM models
- [ ] IDE plugins (VS Code, JetBrains)
- [ ] Auto-tagging with NER (extract framework, language, module from logs)

## License

MIT — use it, fork it, build on it.

---

<p align="center">
  <sub>Built with Claude + ChromaDB + MCP</sub><br/>
  <sub>The more bugs you feed it, the smarter it gets.</sub>
</p>
