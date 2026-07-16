# CodeGuard Implementation Progress

> Status updated on July 16, 2026 on `main`.

## Summary

The GitHub review and Sentry bug-analysis workflows are implemented and covered by mock-based automated tests. Curated RAG works locally and remains optional at runtime. Langfuse has been selected for LLM observability, with implementation still pending. Cloud activation and end-to-end production validation are also pending.

| Area | Status | Outcome |
|---|---|---|
| GitHub webhook and review | Complete | Signed events, allowlist, context retrieval, commit status, PR comment, issue fallback |
| Sentry integration | Complete | HMAC verification, payload parsing, deduplication, structured analysis, manual fallback |
| Async processing | Complete | Redis/RQ queue with connection timeouts and retry |
| LLM orchestration | Complete | OpenAgentic/Groq fallback and structured schema validation |
| Tavily enrichment | Complete | Current security and best-practice fallback references |
| RAG runtime | Complete locally | Deterministic topics, read-only Qdrant retrieval, safe fallback |
| RAG index lifecycle | Complete locally | Seed validation, dry-run, explicit sync, TTL/hash update planning |
| LLM observability | Planned | Langfuse tracing first, with redaction, environment separation, and optional OpenTelemetry integration |
| Local portfolio deployment | Active locally | Docker Compose runs API, worker, and persistent Redis; Tailscale Funnel supplies public HTTPS ingress |
| Cloud/E2E validation | Pending | Railway variables, Qdrant collections, live PR and Sentry verification |

## Current flow

```text
GitHub/Sentry webhook
  -> validation
  -> Redis/RQ
  -> worker and repository context
  -> curated RAG or Tavily fallback
  -> OpenAgentic/Groq fallback
  -> GitHub status, comment, or issue
```

RAG failure does not block analysis. Provider failure produces a controlled fallback rather than exposing raw exceptions or model output.

## Observability status

- Langfuse is the selected platform for LLM-specific tracing and evaluation.
- Initial instrumentation will use one trace per review or analysis job.
- Planned spans cover preprocessing, retrieval, tools/agents, and LLM calls.
- Planned metadata includes model, prompt version, repository, commit SHA, language, status, latency, token usage, and cost.
- Source code, secrets, and PII must be redacted before telemetry is exported.
- Development, staging, and production data will be separated, with production sampling and retention controls.
- OpenTelemetry remains the preferred companion for general application observability and reduced vendor coupling.

No Langfuse instrumentation or production dashboard has been implemented yet.

## Reliability and security completed

- GitHub and Sentry HMAC verification.
- Repository allowlist and repository-name validation.
- PR number and repository-path validation.
- Bounded PR file pagination.
- Defensive GitHub JSON parsing.
- Shared pooled GitHub HTTP client.
- Configurable request timeouts.
- Bounded retry with exponential backoff for GitHub and Redis.
- Sanitized Redis configuration errors.
- Constant-time Sentry signature comparison.
- LLM completion metadata logging without raw output.
- Specific HTTP exception handling and structured failure classification.

## RAG status

- `RAG_ENABLED=true` enables curated retrieval.
- Topic mapping is deterministic and covered for supported language/framework paths.
- Qdrant runtime operations are read-only.
- The seed contains 19 validated documents across seven collections.
- Indexing, sync, and updater commands are safe by default; remote writes require `--execute`.

See [Curated RAG](docs/rag.md) for commands and configuration.

## Latest verification

```text
.venv/bin/pytest -q
209 passed in 0.47s

.venv/bin/python -m compileall -q src tests
passed

git diff --check
passed
```

Unit tests do not require live GitHub, Sentry, Redis, LLM, Tavily, or Qdrant services.

## Next priorities

1. Enforce the repository allowlist in the Sentry webhook before Redis and queue operations.
2. Validate pull-request numbers and changed-file response shapes in the GitHub webhook.
3. Add minimal Langfuse tracing at the LLM boundary with telemetry redaction.
4. Add trace/span coverage for retrieval, tools, and end-to-end review jobs.
5. Run Qdrant target checks and an approved remote seed sync.
6. Enable RAG and observability on Railway, then verify one live PR plus one live Sentry event.

## Documentation

- [Project README](README.md)
- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Setup and Operations](docs/setup.md)
- [Observability](docs/observability.md)
- [Security](docs/security.md)
