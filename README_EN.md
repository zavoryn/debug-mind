**English** | [中文](README.md)

<p align="center">
  <img src="docs/logo.svg" alt="DebugMind" width="120" height="120" />
  <h1 align="center">DebugMind</h1>
  <p align="center">
    <strong>AI Bug Diagnosis Agent with Experiential Memory</strong><br/>
    <em>Every diagnosis makes the next one faster — like a senior engineer who never forgets.</em>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
  <img src="https://img.shields.io/badge/MCP-compatible-purple" alt="MCP Compatible" />
  <img src="https://img.shields.io/badge/tests-198_passed-brightgreen" alt="198 Tests" />
  <img src="https://img.shields.io/badge/hit@1-0.92-orange" alt="hit@1=0.92" />
  <a href="https://github.com/zavoryn/debug-mind/actions/workflows/test.yml"><img src="https://github.com/zavoryn/debug-mind/actions/workflows/test.yml/badge.svg?branch=master" alt="tests" /></a>
  <a href="https://github.com/zavoryn/debug-mind/actions/workflows/lint.yml"><img src="https://github.com/zavoryn/debug-mind/actions/workflows/lint.yml/badge.svg?branch=master" alt="lint" /></a>
  <img src="https://img.shields.io/badge/pypi-pre--release-lightgrey" alt="PyPI pre-release" />
</p>

---

## The Problem

Engineers spend roughly **20% of their time debugging**. A surprising amount of that time goes to bugs that someone on the team already solved — last month, last quarter, last year. That institutional knowledge lives in Slack threads, personal notes, and people's heads, never to be found when you need it.

> The same Redis connection pool exhaustion causing a NullPointerException can be independently debugged three times by three different engineers in a single year.

**DebugMind's core bet:** if every diagnosis is written to a structured knowledge base, and the AI checks that base *before* reasoning from scratch, repeated bugs drop from hours to minutes. The system gets smarter with every case it sees — a flywheel that pure LLM wrappers can't replicate.

---

## Results

Evaluated on 20 real-world bug types across Java, Python, Node.js, and Go:

| Metric | Score |
|--------|-------|
| hit@1 (first result is the correct root cause) | **0.92** |
| hit@3 | 0.97 |
| Test suite | 198 tests, 0 failures |

> **How hit@1 is measured:** a known case is hidden from the database, then its symptom description is used as a query. If the top-ranked result matches the correct root cause, that's a hit. CI enforces hit@1 ≥ 0.85 on every push. See [`docs/EVALUATION.md`](docs/EVALUATION.md).

---

## How It Works

```
Input: symptom description + error log (+ optional: project path)
          │
          ▼
    ① Search memory
       cosine similarity × 0.75 + lexical match × 0.25
       verified cases × 1.0 priority, hit_count log-weighted
          │
    ┌─────┴──────┐
    │ Case found  │              │ No match
    ▼             ▼              ▼
  Load past      ② ReAct loop (up to 20 turns)
  diagnosis         search code → read file → analyze log → reason
  Fast-track fix
    │             │
    └─────┬───────┘
          ▼
    ③ Output: root cause + fix + confidence
          │
          ▼
    ④ Write to memory (Markdown file + vector index)
       hit_count accumulates on each future match
```

---

## Quick Start

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...

# Memory-only mode: just describe the symptoms
debug-mind diagnose "Login endpoint throws NPE intermittently, Redis errors in log"

# Full mode: give it your codebase
debug-mind diagnose \
  --project /path/to/your/project \
  --log error.log \
  --env "java=17,framework=Spring Boot 3.2" \
  "NullPointerException in UserService.login during peak hours"

# Search past cases
debug-mind search "redis connection pool exhausted"

# Launch web UI
debug-mind web
```

---

## Architecture

Five independent layers, each replaceable:

| Layer | Component | Role |
|-------|-----------|------|
| **Memory** | SQLite + Markdown dual-write | Vector search + human-readable persistence; ChromaDB optional |
| **Skills** | ripgrep / tree-sitter | Code search, file reading, project structure analysis |
| **Agent** | ReAct loop | Tool-use driven reasoning; token/cost budget enforced |
| **Protocol** | MCP Server | Exposes memory to Claude Code and any MCP client |
| **Interface** | CLI (Rich) + Gradio Web UI | Streaming terminal output or browser UI |

---

## Key Design Decisions

> The *why* behind each technical choice.

### Why SQLite as the default — not a proper vector database?

ChromaDB and similar tools require C extensions that routinely fail to install in CI environments and on Windows. For the common case (< 5K cases, personal or small-team use), SQLite's linear cosine search completes in ~20ms — fast enough. The backend is abstracted behind an interface; switching to ChromaDB takes one environment variable and zero data migration.

**Tradeoff accepted:** SQLite doesn't scale past ~50K cases. ChromaDB is one `pip install` away when you need it.

### Why Markdown files — aren't vectors enough?

A vector database is a black box. You can't read what's inside it, can't diff it, can't put it in a PR, and if it corrupts you lose everything. Markdown files are human-readable, git-friendly, and independently auditable. The vector index is rebuilt from them on demand (`debug-mind rebuild`).

**Design principle: Markdown is the source of truth. The vector index is a cache.**

### Why hybrid search — not pure semantic vectors?

Semantic embeddings handle natural language well but fail on precise strings: error codes like `ORA-12541`, package names like `org.springframework.beans`, method signatures. These tokens sit far from natural language in embedding space. Adding a lexical term-overlap score (weighted 25%) ensures error codes and class names are exact-matched while the semantic component handles paraphrases.

Final ranking: `0.75 × cosine + 0.25 × lexical`, then multiplied by a verified-case bonus and a `log(hit_count)` weight so the ranking improves automatically over time.

### Why ReAct — not a single-prompt approach?

Bug diagnosis is inherently iterative: read the stack trace → locate the file → discover the real issue is in a transitive dependency → trace back to the config. A single prompt requires the user to front-load all context upfront, which they rarely can. ReAct (Reason + Act) lets the agent decide what to look at next, dynamically. DebugMind caps the loop at 20 turns with a token budget and wall-clock timeout to prevent runaway cost.

### Why verified / hit_count ranking?

Not all stored cases are equally trustworthy. A freshly saved case may be a wrong diagnosis. A human-verified case is more reliable. A case that has been referenced dozens of times is provably useful. Three signals compound into a ranking that improves without manual curation — the system bootstraps its own quality filter.

---

## Memory Format (git-trackable)

Every bug case is a plain Markdown file in `memory/cases/`:

```markdown
# NPE in UserService.login when Redis pool exhausted

> case_id: `abc123` | severity: **high** | status: **fixed**

## Symptoms
Login returns 500, NullPointerException at line 42

## Root Cause
Redis connection pool exhausted → getLoginToken() returns null → NPE

## Fix Suggestion
1. Increase pool size to 32 (currently 8)
2. Add null check before .equals()

## Tags
npe, redis, spring-boot, connection-pool

- verified: true  | hit_count: 7  | last_used_at: 2026-05-20
```

---

## MCP Integration

Connect DebugMind's memory to Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "debug-mind": {
      "command": "python",
      "args": ["-m", "debug_mind.tools.mcp_server"],
      "env": { "DEBUG_MIND_MCP_TOKEN": "your-secret-token" }
    }
  }
}
```

Exposed tools: `search_similar_bugs` · `save_bug_case` · `verify_bug_case` · `get_bug_stats`

---

## Storage Backends

| Backend | Install | Best for |
|---------|---------|----------|
| **SQLite** (default) | nothing extra | personal use, < 5K cases |
| **ChromaDB** | `pip install debug-mind[chroma]` | teams, large knowledge bases |

```bash
DEBUG_MIND_BACKEND=chroma debug-mind rebuild
```

---

## Full Command Reference

```bash
# Diagnosis & search
debug-mind diagnose "description" [--project PATH] [--log FILE] [--env k=v]
debug-mind search "query"          [--top-k 5]
debug-mind list                    [--limit 20]
debug-mind show <case_id>

# Memory management
debug-mind verify <case_id>  --correct | --wrong [--notes "..."]
debug-mind delete <case_id>
debug-mind rebuild            # Rebuild vector index from Markdown files
debug-mind doctor [--fix]    # Check index/file consistency
debug-mind export / import   # Cross-machine memory sharing

# Memory lifecycle
debug-mind decay [--days 30]       # Mark long-unused cases as stale
debug-mind reverify [--days 90]    # List cases due for re-verification
debug-mind link <A> <B> [--relation caused_by|variant|fixed_by]

# Evaluation & audit
debug-mind eval [--search-only]
debug-mind audit [--since 24h] [--op save|verify|delete]

# Integrations
debug-mind serve   # Start MCP server
debug-mind web     # Launch Gradio web UI (default port 7860)
```

---

## Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required |
| `DEBUG_MIND_BACKEND` | `sqlite` | `sqlite` or `chroma` |
| `DEBUG_MIND_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `DEBUG_MIND_MAX_COST` | `0.5` | Max USD per diagnosis |
| `DEBUG_MIND_MAX_TOKENS` | `50000` | Max tokens per diagnosis |
| `DEBUG_MIND_MCP_TOKEN` | — | Auth token for MCP write tools |

Full list in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

---

## The Bigger Picture: From Diagnostic Tool to Autonomous Loop

DebugMind today is **Level 1** — human-triggered, AI gives advice, human applies the fix. The real leverage is automating the entire chain:

```
Level 1 (today)
  Human describes bug → AI diagnoses → Human fixes

Level 2 (near-term)
  Alert/ticket fires → AI diagnoses automatically → Human reviews and merges

Level 3 (end state)
  Alert/ticket fires → AI diagnoses (memory-first) → AI attempts fix → runs tests
                                                            ↓                   ↓
                                                      Tests pass          Tests fail / low confidence
                                                            ↓                   ↓
                                                   Open PR + close ticket   Rollback + escalate
                                                            ↓                   ↓
                                                  Write ✅ success case   Write ❌ failure case
                                                  (instant recall next time)  (never repeat the same wrong path)
```

**Failed attempts are as valuable as successes.** If the AI tries a fix and tests fail, that failed path is written to memory — the next similar bug won't repeat the same wrong approach. The knowledge base isn't just a "correct answers" library; it's a complete map of the diagnostic solution space, including dead ends.

---

## Roadmap

**Done**
- [x] Hybrid vector + lexical search with verified/hit_count ranking
- [x] Pluggable embedding providers (OpenAI, Voyage, BGE, default ONNX)
- [x] MCP server with auth + rate limiting + audit log
- [x] Token/cost budget and wall-clock timeout
- [x] Concurrent write safety (filelock)
- [x] SQLite / ChromaDB dual backend (switchable)
- [x] Gradio web UI + OpenAI provider support
- [x] Memory lifecycle: decay, reverify, case linking
- [x] 198-test suite + CI/CD workflows

**Near-term (Level 2)**
- [ ] PyPI release (`pip install debug-mind`)
- [ ] Hugging Face Spaces live demo
- [ ] Ticket system integration: Lark / Jira / PagerDuty webhook → auto-trigger diagnosis
- [ ] Community benchmark expansion (100+ real bug types)

**Mid-term (Level 3)**
- [ ] Autonomous fix executor: AI generates patch → sandbox test run → opens PR on pass
- [ ] Rollback mechanism: revert on test failure, re-queue ticket with escalation flag
- [ ] Bidirectional memory writes: both successes and failed attempts feed the knowledge graph
- [ ] Multi-project namespaces + RBAC permission isolation

---

## Contributing

```bash
pip install -e ".[dev]"
pytest                        # 198 tests
ruff check src/ tests/        # lint
debug-mind eval --search-only # retrieval quality check (expects hit@1 ≥ 0.85)
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

---

## License

MIT — use it, fork it, build on it.

---

<p align="center">
  <sub>Built with Claude · SQLite · MCP ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="docs/EVALUATION.md">Evaluation</a>
  </sub><br/>
  <sub>The more bugs you feed it, the smarter it gets.</sub>
</p>
