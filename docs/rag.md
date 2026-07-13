# Curated RAG

RAG is optional enrichment for security, framework practices, performance, and code quality. It does not index a target repository and is not required for a review to complete.

## Runtime behavior

When `RAG_ENABLED=true`:

1. `TopicMapper` derives deterministic topics from changed files or a Sentry error.
2. `RAGPipeline` performs read-only filtered queries against Qdrant.
3. High-confidence snippets are added to the prompt.
4. If retrieval is empty or fails, the orchestrator uses Tavily.
5. If enrichment also fails, analysis continues with the repository context alone.

The production request path never embeds, upserts, refreshes, or deletes Qdrant data.

## Environment

```text
RAG_ENABLED=false
QDRANT_URL=https://your-cluster
QDRANT_API_KEY=your-key
RAG_MAX_RESULTS=5
RAG_MIN_CONFIDENCE=0.65
```

`QDRANT_API_KEY` may be omitted only for a trusted local Qdrant instance.

## Read-only checks

```bash
RAG_ENABLED=true python -m src.rag.qdrant_smoke
python -m src.rag.sync --check-target
```

For local Qdrant without an API key:

```bash
RAG_ENABLED=true python -m src.rag.qdrant_smoke --allow-missing-api-key
```

## Local index lifecycle

These commands are safe by default and do not write remotely unless explicitly requested:

```bash
# Validate and preview the curated seed.
python -m src.rag.indexer

# Write the generated local bundle (gitignored).
python -m src.rag.indexer --write

# Preview collection and upsert operations.
python -m src.rag.sync

# Explicitly write to Qdrant after reviewing the plan.
python -m src.rag.sync --execute

# Preview TTL/hash refresh decisions.
python -m src.rag.updater

# Apply approved local updates.
python -m src.rag.updater --updates-file approved_updates.json --write
```

The current seed contains 19 curated documents across seven collections. Cloud configuration and an end-to-end retrieval event still require operational verification.
