# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Memory ablation A/B experiment (`debug-mind eval --ablation`): same cases run
  against a seeded vs an empty store (tools unchanged), with repeat-class /
  novel-class group split and per-run store isolation
- τ-bench-style stability metrics via `--runs k`: pass@k vs pass^k and the
  flakiness gap between them
- Self-learning round-trip (`debug-mind eval --learning-curve`): rounds against
  an initially empty store where only the agent's own saves accumulate —
  measures the memory flywheel end-to-end

### Fixed
- Eval CLI now resolves the API key for the provider selected via
  `DEBUG_MIND_PROVIDER` instead of always requiring `ANTHROPIC_API_KEY`
- Gracefully degraded agent runs (budget/API failures) are now classified as
  `budget_exceeded`/`api_error` in trajectory eval instead of being silently
  counted as wrong answers

## [0.1.0] - 2026-05-19

### Added
- AI-powered bug diagnosis Agent with experiential memory
- MCP server for integration with AI editors and tools
- Memory store with vector search (ChromaDB) and markdown persistence
- Feedback loop: verify correct/wrong to improve future search ranking
- Pluggable embedding providers (default, voyage, openai, bge)
- LLM-based reranker for search result refinement
- Evaluation framework with 12 benchmark cases (hit@1, MRR, keyword recall)
- CLI commands: diagnose, search, list, stats, show, delete, rebuild, serve
- Concurrency safety via filelock on memory writes
- Token/cost budget with graceful partial diagnosis
- Structured logging (JSON/text) with optional OpenTelemetry hook
- API retry with exponential backoff (tenacity)
- MCP token authentication + audit logging
- Input sanitization with configurable size limits
- Doctor command for consistency reconciliation
- Hit-count-weighted search ranking

[0.1.0]: https://github.com/zavoryn/debug-mind/releases/tag/v0.1.0
