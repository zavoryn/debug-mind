# Contributing to DebugMind

> Who this is for: developers who want to fix bugs, add features, or improve documentation.

## Project structure

```
debug-mind/
├── src/debug_mind/       # Main package
│   ├── cli.py            # Click CLI
│   ├── agent.py          # DiagnosticAgent (ReAct loop)
│   ├── schemas.py        # Pydantic models
│   ├── budget.py         # Token/cost budget
│   ├── sanitize.py       # Input sanitization
│   ├── memory/           # MemoryStore, embeddings, reranker
│   ├── tools/            # Anthropic tool schemas + MCP server
│   ├── skills/           # Codebase search/read tools
│   └── observability/    # Structured logging
├── tests/                # pytest test suite
├── evaluation/           # Benchmark dataset and scoring
│   ├── cases/            # YAML benchmark cases
│   └── seed_cases/       # Paired seed markdown files
├── docs/                 # Architecture, development, evaluation guides
├── scripts/              # Utility scripts (bump_version.py)
└── .github/workflows/    # CI/CD pipelines
```

## Getting started

```bash
# Clone and install in editable mode
git clone https://github.com/zavoryn/debug-mind.git
cd debug-mind
pip install -e ".[dev]"

# Run tests
pytest -v

# Run evaluation (no API key needed)
debug-mind eval --search-only
```

## Branch strategy

- `master` — stable, all PRs target this branch
- Feature branches — `feat/short-description` or `fix/short-description`

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new feature
fix: fix a bug
docs: documentation changes
refactor: code restructuring without behavior change
test: add or update tests
chore: CI, tooling, build scripts
```

Examples:
```
feat(ranking): add hit_count log-weighted ranking boost
fix(concurrency): handle lock timeout gracefully in CLI
```

## Testing

- Every new feature must include tests
- Run full suite before pushing: `pytest -v`
- Run eval to verify no search regression: `debug-mind eval --search-only`
- Coverage must not decrease

## Code style

- Ruff handles formatting and linting — `ruff format src/ tests/ && ruff check src/ tests/`
- No unnecessary comments — code should be self-documenting
- Docstrings only for "why", not "what"
- Identifiers in English

## Further reading

- [REFACTOR_PLAN.md](REFACTOR_PLAN.md) — Phase 1 design (schema, memory, evaluation)
- [PHASE_2_PLAN.md](PHASE_2_PLAN.md) — Phase 2 design (production hardening)
- [PHASE_3_PLAN.md](PHASE_3_PLAN.md) — Phase 3 design (open-source engineering)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Architecture overview
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — How to extend DebugMind
- [docs/EVALUATION.md](docs/EVALUATION.md) — Evaluation framework details
