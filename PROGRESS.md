# CodeGuard AI — Implementation Progress

> Implementation status based on the `develop` branch as of July 13, 2026.
> Last verified at commit `115040a` (`TTL knowledge updater`).

---

## Status Summary

CodeGuard AI now has a complete primary workflow for receiving GitHub and Sentry
webhooks, processing analyses asynchronously, enriching prompts with RAG or
Tavily, running the LLM fallback chain, and sending results back to GitHub.

| Phase | Status | Main outcome |
|---|---|---|
| 1–8 — Core MVP | ✅ Complete | Scanner, API, GitHub webhook, context builder, orchestration, and GitHub output |
| 9–11 — Deployment and async processing | ✅ Implemented | Railway/Docker workflow, Tavily enrichment, Redis + RQ worker |
| 12 — Sentry integration | ✅ Complete | HMAC, error parsing, Redis deduplication, structured bug analysis, GitHub Issue fallback |
| 13 — Tests and hardening | ✅ Complete | Webhook validation, schema validation, provider fallback, commit status checks, 145 tests |
| 14.1–14.6 — RAG MVP runtime | ✅ Complete | Topic mapper, read-only retrieval, prompt integration, safety tests, curated seed |
| 14.7 — Qdrant Cloud wiring | 🟡 Implemented; operational check required | Client and smoke diagnostics are available; Railway environment and cloud E2E flow still require verification |
| 14.8 — Indexer and sync | ✅ Complete locally | Indexer, safe dry-run, target check, and explicit remote execution |
| 14.9 — TTL/hash updater | ✅ Complete locally | Refresh planner, TTL/hash comparison, quality gate, and explicit write mode |

Overall status: **The MVP is active; local RAG implementation is complete, while
cloud activation still requires operational validation.**

---

## Current Architecture

```text
GitHub PR/push                 Sentry error
      |                             |
      +-------- FastAPI webhook ----+
                    |
          signature + payload validation
                    |
              Redis / RQ queue
                    |
                 Worker
                    |
             Context Builder
                    |
               Orchestrator
          +---------+----------+
          |                    |
   Curated RAG/Qdrant     Tavily fallback
          +---------+----------+
                    |
          OpenAgentic/Groq fallback
                    |
      +-------------+-------------+
      |                           |
 PR comment + commit status   GitHub bug issue
```

RAG remains optional. If it is disabled, not configured, or Qdrant fails, the
analysis continues with Tavily or the standard prompt.

---

## Completed Implementation

### Core review and GitHub integration

- The scanner supports Python, JavaScript/TypeScript, PHP, Java, Go, C#, Razor,
  Twig, and C++, with centralized directory and file filtering in
  `src/config.py`.
- The GitHub webhook supports `push` and `pull_request` events (`opened` and
  `synchronize`), including PR file pagination.
- The GitHub webhook is protected by HMAC SHA-256 and a repository allowlist.
- Malformed payloads, invalid signatures, and unauthorized repositories are
  rejected before a job is created.
- `ContextBuilder` retrieves changed and related files through the GitHub API.
- Reviews are processed by an RQ worker, allowing the webhook to return HTTP 202.
- The worker publishes `pending`, `success`, `failure`, or `error` commit statuses.
- Review results are posted as PR comments; a GitHub Issue is used when no open
  PR is available.

### LLM orchestration and enrichment

- The provider fallback chain is active through OpenAgentic and Groq.
- PR reviews use text output, while Sentry bug analysis uses structured JSON
  output validated against the `BugAnalysis` schema.
- LLM envelope and output parsing are centralized for consistent behavior across
  providers.
- Tavily provides real-time security and best-practice references.
- Curated RAG is attempted first; Tavily is used when RAG returns no results or
  fails.

### Sentry bug agent

- `Sentry-Hook-Signature` is verified against the raw request body.
- Irrelevant events and resources are skipped safely.
- Stack traces are mapped to source files to build the analysis context.
- `issue_id` values are deduplicated through Redis with a pending lock and TTL.
- Valid analyses create GitHub Issues labeled `bug` and `ai-analyzed`.
- If every provider or schema validation attempt fails, the worker still creates
  a manual fallback Issue labeled `needs-manual-review`.

### Deployment and reliability

- The Dockerfile, Procfile, and port configuration support Railway deployment.
- Redis URLs support Railway configuration through `REDIS_URL`, private/public
  URLs, or host/port/password components, with connection timeouts.
- Webhook, worker, GitHub client, prompt, schema, and RAG paths have mock-based
  tests that do not require paid or external services during unit testing.

---

## Phase 14 — RAG Pipeline

### 14.1–14.6: runtime MVP — complete

- The runtime contract is strictly separated from indexing and update paths.
- `TopicMapper` deterministically selects the language, framework, category,
  topics, and collections for PR and Sentry contexts.
- `QdrantRuntimeClient` only performs read/filter queries; no embedding, upsert,
  or delete operation occurs in the production request path.
- `RAGPipeline` limits results, applies the minimum confidence threshold, formats
  concise snippets, and handles failures without stopping the analysis.
- The orchestrator injects RAG snippets into review and bug-analysis prompts.
- The curated seed contains **19 documents** for PHP/Laravel, Python/FastAPI,
  JavaScript/Node.js, and general code quality.
- Each seed document has validated topic/category/language/framework, source, and
  confidence metadata; the indexer adds TTL, timestamp, and content hash fields
  to the point bundle.

### 14.7: Qdrant Cloud wiring — operational validation required

Available now:

- Environment contract: `QDRANT_URL`, `QDRANT_API_KEY`, `RAG_ENABLED`,
  `RAG_MAX_RESULTS`, and `RAG_MIN_CONFIDENCE`.
- Read-only smoke command: `python -m src.rag.qdrant_smoke`.
- Structured logs: `rag_query_started`, `rag_query_succeeded`, and
  `rag_query_failed`.

Still to be verified in the external environment:

- The Railway worker has all required RAG variables configured correctly.
- Qdrant Cloud contains the seven collections produced by the seed sync.
- At least one PR or Sentry event successfully retrieves a RAG snippet in an E2E
  test.

### 14.8: local indexer and sync — complete locally

- `python -m src.rag.indexer` validates and prepares the point bundle.
- `python -m src.rag.sync` is safe by default and only displays a dry-run plan.
- `--check-target` only reads the target state.
- `--execute` is explicitly required to create collections or upsert points into
  Qdrant Cloud.
- The MVP uses metadata/filter retrieval with a one-dimensional placeholder
  vector; semantic embedding is not yet a runtime dependency.

### 14.9: TTL/hash updater — complete locally

- `python -m src.rag.updater` builds a refresh plan without changing files.
- Fresh topics are skipped until their TTL expires.
- Expired topics can be refreshed from approved local updates and/or Tavily.
- Unchanged content only updates timestamp metadata.
- Changed content is marked for re-indexing and synchronization.
- The quality gate rejects invalid refresh sources or content.
- Seed changes only occur through `--write`; the updater does not run in the
  production PR/Sentry request path.

---

## Latest Verification

Performed on July 13, 2026:

```text
.venv/bin/pytest -q
145 passed in 0.56s

.venv/bin/python -m src.rag.indexer
19 points; 7 collections; dry-run successful

.venv/bin/python -m src.rag.sync
19 points planned; 0 upserted; dry-run successful

.venv/bin/python -m src.rag.updater
19 expired_no_refresh; requires_reindex=false; dry-run successful
```

Updater note: all seed documents currently exceed their TTL, but the dry-run was
not given a refresh provider or new content. Therefore, `expired_no_refresh` is
the expected result, and the seed remains unchanged.

---

## Current Project Structure

```text
src/
├── agents/sentry_agent.py
├── api/
│   ├── main.py
│   └── webhook.py
├── context/context_builder.py
├── github/
│   ├── github_client.py
│   └── repo_policy.py
├── orchestration/
│   ├── orchestrator.py
│   ├── prompts.py
│   ├── schemas.py
│   └── tavily_client.py
├── rag/
│   ├── indexer.py
│   ├── knowledge_base.py
│   ├── qdrant_client.py
│   ├── qdrant_smoke.py
│   ├── rag_pipeline.py
│   ├── sync.py
│   ├── topic_mapper.py
│   ├── updater.py
│   └── seeds/mvp_seed.json
├── utils/formatters.py
└── worker/worker.py

tests/
├── test_github_client.py
├── test_orchestrator.py
├── test_prompts.py
├── test_rag_indexer_sync.py
├── test_rag_runtime.py
├── test_rag_seed.py
├── test_rag_updater.py
├── test_sentry_agent.py
├── test_topic_mapper.py
├── test_webhook.py
└── test_worker.py
```

---

## Next Priorities

1. Run `python -m src.rag.sync --check-target` against Qdrant Cloud.
2. Review the dry-run, then run `python -m src.rag.sync --execute` only after the
   target and remote-write risk have been approved.
3. Enable `RAG_ENABLED=true` on the Railway worker and run the smoke query.
4. Test one PR and one Sentry event E2E; verify retrieval logs and GitHub output
   when RAG succeeds and when it fails.
5. Prepare approved refresh content for the 19 seed documents whose TTL has
   expired, then review the updater plan before using `--write` and syncing again.
6. After RAG operations are stable, introduce semantic retrieval and embedding as
   a separate enhancement rather than a required runtime dependency.

---

## Environment Variables

```text
# Core
GITHUB_PAT_TOKEN
GITHUB_WEBHOOK_SECRET
CODEGUARD_ALLOWED_REPOS
REDIS_URL
OPENAGENTIC_API_KEY
GROQ_API_KEY

# Sentry and enrichment
SENTRY_CLIENT_SECRET
TAVILY_API_KEY

# Optional RAG
QDRANT_URL
QDRANT_API_KEY
RAG_ENABLED=false
RAG_MAX_RESULTS=5
RAG_MIN_CONFIDENCE=0.65
```

Never commit secrets or the `.env` file to the repository.
