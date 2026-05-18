# Contributing to DebugMind

Thanks for your interest! Here's how to get started.

## Development Setup

```bash
# Clone and install
git clone https://github.com/zavoryn/debug-mind.git
cd debug-mind
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/
```

## How to Contribute

### Adding a Bug Case

The easiest contribution — add a real bug case to `memory/examples/`:

```markdown
# Your Bug Title

> case_id: `yourname001` | severity: **medium** | status: **fixed**

## Environment
- language: ...
- framework: ...

## Symptoms
What happened...

## Root Cause
What was actually wrong...

## Fix Suggestion
How you fixed it...

## Tags
comma, separated, tags
```

### Adding a Skill

Skills live in `src/debug_mind/skills/`. Each skill is a module with functions that return dicts (compatible with Claude's tool result format).

1. Create your skill module (e.g. `src/debug_mind/skills/database_skill.py`)
2. Add the tool definition in `src/debug_mind/agent.py`
3. Wire up the execution in `DiagnosticAgent._execute_tool()`

### Reporting Issues

Use the issue templates — bug reports and feature requests are both welcome.

## Code Style

- Follow PEP 8 (enforced by ruff)
- Use type hints on all function signatures
- Keep functions focused — one responsibility each
- No comments that say what the code does; comments are for WHY

## Pull Request Process

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Ensure tests pass (`pytest`)
5. Ensure lint passes (`ruff check`)
6. Open a PR with a clear description
