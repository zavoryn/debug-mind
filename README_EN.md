**English** | [中文](README.md)

<p align="center">
  <img src="docs/logo.svg" alt="DebugMind" width="120" height="120" />
  <h1 align="center">DebugMind</h1>
  <p align="center">
    <strong>AI Bug Diagnosis Agent with Experiential Memory</strong><br/>
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

Every debugging session starts from scratch. You hit a bug, search Google, dig through logs — even if someone on your team solved the exact same issue last week.

**What if your debugging tool remembered every bug it ever diagnosed?**

## How It Works

```
┌──────────────────────────────────────────────────────────┐
│                       Bug Report                          │
│         "NPE on login endpoint, Redis errors in log"      │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
               ┌───────────────┐
               │ Memory Search  │  ◄── vector similarity + keyword match
               │  "Seen this?" │      (SQLite, zero extra install)
               └───────┬───────┘
                       │
           ┌───────────┼───────────┐
           ▼                       ▼
    [Similar case found]     [No match]
           │                       │
    Load past diagnosis       Full AI diagnosis
    + Fast-track fix          + Systematic root-cause analysis
           │                       │
           └───────────┬───────────┘
                       ▼
               ┌───────────────────┐
               │  Diagnosis + Fix  │
               └───────┬───────────┘
                       │
                       ▼
               ┌───────────────────┐
               │  Save to Memory   │  ──► Markdown file (git-friendly)
               │  for next time    │  ──► Vector embedding (searchable)
               └───────────────────┘
```

Every diagnosis makes the next one faster. Verified cases rise in search ranking; unused ones decay over time, keeping the knowledge base fresh.

## Quick Start

```bash
# Install — no C extensions, works on any platform
pip install -e .

# Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Diagnose a bug (memory-only mode, no codebase needed)
debug-mind diagnose "Service returns 500 intermittently"

# Diagnose with codebase access (the real power)
debug-mind diagnose \
  --project /path/to/your/project \
  --log error.log \
  --env "java=17,framework=Spring Boot 3.2" \
  "NullPointerException in UserService.login during peak hours"

# Search past diagnoses
debug-mind search "redis connection timeout"

# Show memory health
debug-mind doctor
```

## Architecture

DebugMind is built in five layers, each independently useful:

| Layer | Component | What It Does |
|-------|-----------|--------------|
| **Memory** | SQLite + Markdown | Default: pure Python, zero extra deps. Optional: ChromaDB for HNSW indexing at scale. |
| **Skills** | ripgrep / grep | Real code search, file reading, project structure analysis |
| **Agent** | Claude + ReAct Loop | Tool-use driven diagnostic reasoning (20-turn max, cost-budgeted) |
| **Protocol** | MCP Server | Expose memory to any MCP-compatible client |
| **Interface** | CLI (Rich) + Web UI | Terminal with live streaming output, or Gradio web interface |

### Design Decisions

- **SQLite is the default** — pure Python stdlib, installs instantly, works everywhere
- **ChromaDB is optional** — `pip install debug-mind[chroma]` for HNSW index when you have 10K+ cases
- **Markdown is the source of truth** — vector index can always be rebuilt from it; files are git-friendly
- **MCP protocol** makes the memory accessible from Claude Code, Claude Desktop, or any MCP client
- **Memory improves over time** — `verified` cases boost ranking; `hit_count` log-weights frequently useful cases; stale cases decay

## Storage Backends

| Backend | Install | Best for |
|---------|---------|----------|
| **SQLite** (default) | nothing extra | personal use, <5K cases, any platform |
| **ChromaDB** | `pip install debug-mind[chroma]` | teams, large knowledge bases, faster HNSW search |

Switch backends with one env var — your Markdown cases are preserved either way:

```bash
# Use ChromaDB
DEBUG_MIND_BACKEND=chroma debug-mind rebuild
```

## Full Command Reference

```bash
# Diagnosis
debug-mind diagnose "description" [--project PATH] [--log FILE] [--env k=v,k=v]
                                   [--severity critical|high|medium|low]
                                   [--max-cost 0.5] [--max-tokens 50000]

# Memory search & browse
debug-mind search "query"          [--top-k 5]
debug-mind list                    [--limit 20]
debug-mind show <case_id>
debug-mind stats

# Memory management
debug-mind verify <case_id>        --correct | --wrong [--notes "..."]
debug-mind delete <case_id>
debug-mind rebuild                 # Rebuild vector index from Markdown files
debug-mind doctor                  # Check index/file consistency [--fix]
debug-mind dedupe                  # Find near-duplicate cases

# Backup & sharing
debug-mind export                  [--output cases.json] [--limit N]
debug-mind import cases.json       [--skip-existing] [--dry-run]

# Memory lifecycle (Phase 5)
debug-mind decay                   [--days 30] [--dry-run]
debug-mind reverify                [--days 90]
debug-mind link <case_a> <case_b>  [--relation variant|caused_by|fixed_by|related]

# Evaluation
debug-mind eval                    [--search-only] [--case ID] [--json out.json]

# Audit log
debug-mind audit                   [--since 1h|24h|7d] [--op save|verify|delete]

# Integrations
debug-mind serve                   # Start MCP server
debug-mind web                     # Launch Gradio web UI [--port 7860]
```

## MCP Integration

Connect DebugMind's memory to Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "debug-mind": {
      "command": "python",
      "args": ["-m", "debug_mind.tools.mcp_server"],
      "env": {
        "DEBUG_MIND_MCP_TOKEN": "your-secret-token"
      }
    }
  }
}
```

MCP tools exposed: `search_similar_bugs`, `save_bug_case`, `list_recent_bugs`, `get_bug_stats`, `verify_bug_case`, `delete_bug_case`

## Memory Format

Every bug case is a Markdown file in `memory/cases/`:

```markdown
# NPE in UserService.login when Redis pool exhausted

> case_id: `abc123` | severity: **high** | status: **fixed** | verified: ✅

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

Files are version-controllable, human-readable, and rebuildable with `debug-mind rebuild`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required) |
| `DEBUG_MIND_MEMORY_DIR` | `./memory` | Where to store cases and index |
| `DEBUG_MIND_BACKEND` | `sqlite` | Storage backend: `sqlite` or `chroma` |
| `DEBUG_MIND_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `DEBUG_MIND_EMBEDDING` | `default` | Embedding: `default`, `openai`, `voyage`, `bge` |
| `DEBUG_MIND_MAX_COST` | `0.5` | Max cost per diagnosis in USD |
| `DEBUG_MIND_MAX_TOKENS` | `50000` | Max cumulative tokens per diagnosis |
| `DEBUG_MIND_MAX_WALL_SECS` | `300` | Wall-clock timeout per diagnosis (seconds) |
| `DEBUG_MIND_LOG_FORMAT` | `text` | Log format: `text` or `json` |
| `DEBUG_MIND_MCP_TOKEN` | — | Auth token for MCP write tools |
| `DEBUG_MIND_MCP_RATE_LIMIT` | `60` | Max MCP write requests per minute |
| `DEBUG_MIND_AUDIT_MAX_BYTES` | `52428800` | Audit log rotation size (50 MiB) |

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| LLM | Claude (Anthropic) | Best-in-class tool use; OpenAI compatible via `[openai]` extra |
| Agent loop | Custom ReAct | 20-turn max, token/cost budget, wall-clock timeout |
| Default vector store | SQLite (stdlib) | Pure Python, linear cosine search, zero extra deps |
| Optional vector store | ChromaDB | HNSW index, recommended for >5K cases |
| Persistence | Markdown files | Git-friendly, source of truth for all cases |
| Embedding | all-MiniLM-L6-v2 | Via ChromaDB default; swappable with OpenAI/Voyage/BGE |
| Protocol | MCP | Standard for AI tool integration |
| CLI | Click + Rich | Live streaming output, turn-by-turn progress |
| Web UI | Gradio | Optional (`pip install debug-mind[web]`) |
| Code search | ripgrep / grep | Real project code access during diagnosis |

## Use Cases

**Personal debugging assistant** — Build a local memory of every bug you diagnose. Next time you hit something similar, DebugMind finds it in seconds and skips the boilerplate analysis.

**Team knowledge base** — Export your memory with `debug-mind export` and share via git or import on another machine. Everyone's diagnoses contribute to a shared knowledge pool.

**CI/CD integration** — Feed build failures into DebugMind. If a test fails with an error you've seen before, it tells you immediately.

**MCP memory for Claude** — Run `debug-mind serve` and Claude Code gains persistent bug knowledge that outlives any single conversation.

## Evaluation

DebugMind ships with a 50-case benchmark covering Java, Python, Node, Go, and C#:

```bash
# Evaluate retrieval quality (no API key needed)
debug-mind eval --search-only

# Full end-to-end evaluation
debug-mind eval
```

Baseline metrics (ChromaDB + default embedding): hit@1=0.92, hit@3=1.00, MRR=0.96

## Roadmap

- [x] Hybrid vector + lexical search with verified/hit_count ranking
- [x] Pluggable embedding providers (OpenAI, Voyage, BGE, default)
- [x] MCP server with auth + rate limiting + audit log
- [x] Token/cost budget and wall-clock timeout
- [x] Concurrent write safety (filelock)
- [x] ChromaDB and SQLite backends (switchable)
- [x] Gradio web UI
- [x] OpenAI provider support
- [x] Memory lifecycle: decay, reverify, case linking
- [x] CI/CD workflows + 198-test suite
- [x] Export/import for cross-machine memory sharing
- [ ] PyPI release (`pip install debug-mind`)
- [ ] Multi-project memory namespaces
- [ ] IDE plugins (VS Code, JetBrains)
- [ ] Community benchmark expansion (100+ cases)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, code style, and how to add benchmark cases or new skills.

```bash
pip install -e ".[dev]"
pytest                        # 198 tests
ruff check src/ tests/        # lint
debug-mind eval --search-only # retrieval quality check
```

## License

MIT — use it, fork it, build on it.

---

<p align="center">
  <sub>Built with Claude · SQLite · MCP</sub><br/>
  <sub>The more bugs you feed it, the smarter it gets.</sub>
</p>
