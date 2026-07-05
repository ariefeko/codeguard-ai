# Phase 14 RAG — Execution Plan

Source guide: `snapshot_phase14_rag.md` rev4.

This document turns the Phase 14 RAG guide into staged engineering work. It is intentionally scoped so the MVP can be shipped safely without turning RAG into a hard dependency for CodeGuard.

---

## Non-Negotiable Rules

- Do not commit markdown planning files.
- RAG is not for indexing the full repository codebase.
- RAG is a curated knowledge base for security, best practices, code quality, performance, and framework patterns.
- Production MVP uses predefined topic retrieval.
- Production runtime only queries/filters Qdrant.
- Railway production must not embed, refresh, update, or sync Qdrant documents.
- Ollama/nomic is local-only for indexing and knowledge refresh.
- Tavily/docs refresh belongs to local refresh/indexing jobs, not the main runtime request path.
- Multi-collection remains allowed for MVP.
- All RAG and Qdrant scripts must live inside `src/rag/`.
- Prompt injection is limited to 3-6 short, high-confidence snippets.
- Fallback is mandatory: RAG failed -> Tavily -> normal prompt.
- LLM analysis must continue as long as core PR/Sentry context exists.

---

## Final Principle

```text
Changed files explain what changed.
RAG explains what matters: security, best practices, code quality, framework patterns.
Tavily checks what changed recently.
LLM connects the evidence into useful review.
```

---

## Mini-Delivery Workflow

Every phase is treated as a small delivery with its own verification gate.

```text
implement one phase
-> run phase-specific tests
-> run related regression tests
-> report result
-> wait for approval before continuing to the next phase
```

Rules:
- Do not continue automatically to the next phase when the current phase has not been verified.
- Each phase must have a clear test/checklist result before moving on.
- If a phase cannot be tested automatically, use a manual checklist and document the residual risk.
- Keep the change set small enough that rollback is obvious.
- Prefer mocked external dependencies for unit tests.
- Qdrant Cloud, Tavily, Ollama, and Railway checks are integration/manual tests unless explicitly scoped otherwise.

---

## Phase 14.1 — Design Lock

Goal: Freeze the MVP contract before implementation.

Work:
- Confirm collection names and whether MVP starts with all planned collections or a Laravel/PHP subset.
- Confirm minimum Qdrant payload metadata.
- Confirm environment variables.
- Confirm fallback behavior.
- Confirm prompt format and snippet budget.

Rules:
- No runtime embedding in Railway.
- No updater or sync logic in this phase.
- No root-level scripts.
- Keep decisions compatible with `snapshot_phase14_rag.md` Final Decision.

Exit Criteria:
- We have a clear runtime contract: input context -> topic mapping -> Qdrant filter query -> prompt snippets.
- We know which collections/topics are required for the first manual test.

Test Gate:
- No code test required.
- Validate the contract checklist: metadata, env vars, fallback, runtime/update split, collection/topic scope.

---

## Phase 14.2 — Topic Mapper MVP

Goal: Build the deterministic mapper that replaces free-form query embedding in production.

Work:
- Detect language from changed file paths and extensions.
- Detect framework from file paths/content hints where practical.
- Map PR context to category and topics.
- Map Sentry error type/file path to category and topics.
- Validate parent category and fallback when topic/category mismatch.

Rules:
- Keep mapper deterministic and testable.
- Prefer conservative topic matches over noisy matches.
- Wrong-parent topics must move to a better parent or fallback to general.
- No Qdrant dependency inside mapper.

Exit Criteria:
- Unit tests cover PHP/Laravel, Python/FastAPI, JS/TS, unknown fallback, and Sentry error mapping.

Test Gate:
- Run topic mapper unit tests.
- Confirm no Qdrant/Tavily/Ollama dependency is needed for mapper tests.

---

## Phase 14.3 — RAG Runtime Read-Only

Goal: Add a runtime RAG interface that only reads from Qdrant.

Work:
- Create `src/rag/qdrant_client.py`.
- Create `src/rag/rag_pipeline.py`.
- Query by metadata/filter using selected collections and topics.
- Normalize Qdrant results into short knowledge snippets.
- Implement safe failure behavior.

Rules:
- Runtime must not embed.
- Runtime must not upsert/update/delete Qdrant documents.
- Runtime must not call local Ollama.
- Runtime must be optional via `RAG_ENABLED`.
- Qdrant failure must return empty RAG results, not crash analysis.

Exit Criteria:
- A mocked Qdrant response can produce prompt-ready snippets.
- A mocked Qdrant failure falls back cleanly.

Test Gate:
- Run RAG runtime unit tests with mocked Qdrant responses.
- Confirm Qdrant failures return empty results instead of raising into analysis flow.
- Confirm runtime code path has no embedding/update/sync behavior.

---

## Phase 14.4 — Prompt and Orchestrator Integration

Goal: Inject RAG enrichment into existing review and bug-fix flows.

Work:
- Initialize RAG runtime in `Orchestrator`.
- Add RAG results to `_enrich_with_search()`.
- Use Tavily only when RAG returns empty/failed or when realtime enrichment is still needed.
- Update prompt builder to include compact RAG snippets.

Rules:
- RAG is enrichment, not a hard dependency.
- Keep existing Tavily behavior as fallback.
- Keep existing LLM fallback chain untouched.
- Keep prompt snippets short and source-aware.
- Do not let RAG content crowd out changed files and Sentry evidence.

Exit Criteria:
- PR review prompt includes RAG snippets when available.
- Sentry bug prompt includes RAG snippets when available.
- Normal analysis still works when RAG is disabled or unavailable.

Test Gate:
- Run orchestrator unit tests.
- Run prompt builder tests.
- Confirm RAG success path, RAG disabled path, and RAG failure -> Tavily fallback path.
- Run existing review/Sentry regression tests related to orchestration.

---

## Phase 14.5 — Tests

Goal: Prove the MVP is safe before deployment.

Work:
- Add unit tests for topic mapper.
- Add unit tests for RAG query result formatting.
- Add unit tests for Qdrant failure fallback.
- Add orchestrator tests for RAG injection and Tavily fallback.

Rules:
- Mock network calls.
- Do not require Qdrant Cloud in unit tests.
- Do not require Ollama in unit tests.
- Keep current tests passing.

Exit Criteria:
- Topic mapping behavior is covered.
- RAG failure path is covered.
- Existing review and Sentry flows still pass.

Test Gate:
- Run the relevant test suite.
- If the local environment cannot run tests, report the exact missing dependency/tool and do not mark the phase fully verified.

---

## Phase 14.6 — Initial Knowledge Seed

Goal: Create the first curated knowledge set for MVP validation.

Work:
- Start with curated PHP, Python, and Node.js knowledge for MVP validation.
- Seed topics such as SQL injection, authorization checks, exception handling, missing null handling, Eloquent best practices, CSRF/XSS basics.
- Store source metadata and concise guidance.

Rules:
- Do not dump raw web/docs content into Qdrant.
- Every document needs source title and source URL.
- Prefer trusted sources: OWASP, CWE/MITRE, official framework docs, vendor docs, verified internal notes.
- Keep snippets concise and directly useful.

Exit Criteria:
- At least one useful topic exists for common Laravel PR review and Sentry bug flows.

Test Gate:
- Validate seed schema.
- Manually inspect snippet quality and source metadata.
- Confirm at least one Laravel/PHP topic can be retrieved in a local/mock flow.

---

## Phase 14.7 — Qdrant Cloud Wiring

Goal: Connect production runtime to Qdrant Cloud.

Work:
- Configure Railway worker variables:
  - `QDRANT_URL`
  - `QDRANT_API_KEY`
  - `RAG_ENABLED=true`
  - `RAG_MAX_RESULTS`
  - `RAG_MIN_CONFIDENCE`
- Run the read-only smoke command:
  - `python -m src.rag.qdrant_smoke`
- Verify worker can query Qdrant Cloud.
- Run one end-to-end PR/Sentry test.

Rules:
- Production still only queries.
- No embedding service is required in production.
- If Qdrant Cloud is unavailable, CodeGuard must still analyze with Tavily/normal prompt.

Exit Criteria:
- Railway worker logs show `rag_query_started` plus `rag_query_succeeded`
  or `rag_query_failed`.
- Local/worker smoke logs show either `rag_qdrant_smoke_ok` or
  `Qdrant connection succeeded but no collections are indexed yet`.
  The latter confirms 14.7 cloud wiring and hands collection creation to 14.8.
- GitHub PR/Sentry output remains usable.

Test Gate:
- Run Qdrant Cloud smoke query.
- Verify Railway env vars exist on the worker service.
- Run one manual end-to-end PR or Sentry test.

---

## Phase 14.8 — Local Indexer and Sync

Goal: Add local tooling for indexing curated knowledge and syncing to cloud.

Work:
- Create `src/rag/indexer.py`.
- Create `src/rag/sync.py`.
- Use local Ollama/nomic for embeddings if vector embeddings are needed.
- MVP metadata/filter retrieval may use a one-dimensional placeholder vector for
  Qdrant collection compatibility until semantic retrieval is introduced.
- Keep sync safe by default:
  - `python -m src.rag.sync` performs a dry-run only.
  - `python -m src.rag.sync --check-target` performs a read-only target check.
  - `python -m src.rag.sync --execute` performs remote writes after explicit approval.
- Keep command entry points as:
  - `python -m src.rag.indexer`
  - `python -m src.rag.sync`

Rules:
- Scripts stay inside `src/rag/`.
- No production runtime dependency on indexer/sync.
- Do not scatter standalone scripts in project root.

Exit Criteria:
- Local indexing can prepare/update knowledge.
- Sync can plan and push prepared knowledge to Qdrant Cloud.
- Remote sync execution must be explicitly approved because it writes curated local data
  to an external Qdrant target.

Test Gate:
- Run indexer dry-run.
- Run sync with mocked or explicitly approved target first.
- Confirm scripts are invoked through `python -m src.rag...` and remain inside `src/rag/`.

---

## Phase 14.9 — TTL/Hash Updater

Goal: Add refresh automation without touching production runtime.

Work:
- Create `src/rag/updater.py`.
- Check TTL per topic.
- Use Tavily/docs refresh only for expired topics.
- Hash cleaned content.
- Update timestamp only when content hash is unchanged.
- Re-embed and sync only when curated content changes.

Rules:
- Updater does not run in PR/Sentry request path.
- Updater should be local/manual or scheduled separately.
- Bad source quality must be rejected before storing.

Exit Criteria:
- Fresh topics skip refresh.
- Expired unchanged topics update metadata only.
- Expired changed topics prepare updated content for sync.

Test Gate:
- Run TTL/hash unit tests.
- Confirm fresh TTL skip behavior.
- Confirm unchanged content updates metadata only.
- Confirm changed content enters re-index path without running in production runtime.

---

## Recommended Work Order

```text
14.1 Design Lock
14.2 Topic Mapper MVP
14.3 RAG Runtime Read-Only
14.4 Prompt and Orchestrator Integration
14.5 Tests
14.6 Initial Knowledge Seed
14.7 Qdrant Cloud Wiring
14.8 Local Indexer and Sync
14.9 TTL/Hash Updater
```

For the first MVP slice, stop after `14.5` unless the runtime path is stable. Then continue with seed data and Qdrant Cloud wiring.
