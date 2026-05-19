# Embedding & Reranking Configuration

DebugMind uses a pluggable embedding system and optional reranker to improve memory recall quality.

## Embedding Providers

Set `DEBUG_MIND_EMBEDDING` environment variable to select a provider:

| Provider | Value | Requirements |
|----------|-------|--------------|
| Default (MiniLM) | `default` | None (built-in) |
| Voyage AI | `voyage` | `VOYAGE_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| BGE-M3 | `bge` | `sentence-transformers` package |

If a provider's dependencies or API keys are missing, DebugMind falls back to the default with a warning.

## Reranker

Set `DEBUG_MIND_RERANK` environment variable:

| Value | Behavior |
|-------|----------|
| `none` (default) | No reranking |
| `llm` | Uses Claude Haiku to score candidates 1-10 |

## Comparison Example

```bash
# Baseline with default settings
debug-mind eval --search-only

# Try with LLM reranker
DEBUG_MIND_RERANK=llm debug-mind eval --search-only

# Compare with different embedding (if API key available)
DEBUG_MIND_EMBEDDING=openai debug-mind eval --search-only
```

## Optional Dependencies

To install optional embedding providers:

```bash
pip install voyageai           # For Voyage AI
pip install openai             # For OpenAI
pip install sentence-transformers  # For BGE-M3
```
