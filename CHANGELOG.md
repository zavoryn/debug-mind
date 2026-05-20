# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
