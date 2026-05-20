# Development Guide

> Who this is for: contributors adding new tools, embedding backends, or rerankers.

## Adding a new tool

1. **Define the schema** in `src/debug_mind/tools/schemas.py`:
   ```python
   # In MEMORY_TOOLS or CODEBASE_TOOLS list
   {
       "name": "my_new_tool",
       "description": "What this tool does",
       "input_schema": {
           "type": "object",
           "properties": {
               "param1": {"type": "string", "description": "..."},
           },
           "required": ["param1"],
       },
   }
   ```

2. **Add the executor** in `src/debug_mind/agent.py` — find the `_execute_tool` method and add an `elif` branch.

3. **Register in MCP** (if memory tool) in `src/debug_mind/tools/mcp_server.py`.

4. **Add tests** following the patterns in `tests/test_agent.py`.

## Adding a new embedding provider

1. Implement a function matching the `EmbeddingFunction` protocol in `src/debug_mind/memory/embeddings.py`:
   ```python
   def my_embed(texts: list[str]) -> list[list[float]]:
       ...
       return vectors
   ```

2. Register it in `_get_embedding_functions()` dict.

3. Add fallback logic in `make_embedding()` — check for API key, import availability, etc.

See `voyage_embed`, `openai_embed`, `bge_embed` as examples.

## Adding a new reranker

1. Create a class implementing the reranker protocol in `src/debug_mind/memory/rerank.py`:
   ```python
   class MyReranker:
       def rerank(
           self, query: str, results: list[SearchResult], top_k: int = 10
       ) -> list[SearchResult]:
           ...
           return reranked
   ```

2. Register in `make_reranker()` with an env var trigger (e.g., `DEBUG_MIND_RERANK=my_reranker`).

See `IdentityReranker` and `LLMReranker` as examples.

## Running evaluation

```bash
# Search-only (no API key needed)
debug-mind eval --search-only

# Single case
debug-mind eval --case npe-null-check

# JSON output for CI
debug-mind eval --search-only --json
```

## Before submitting a PR

```bash
ruff check src/ tests/
ruff format --check src/ tests/
pytest -v
debug-mind eval --search-only
```
