# Architecture

## Runtime flow

```text
GitHub push/PR                 Sentry error
      |                             |
      +------ FastAPI webhooks -----+
                    |
          signature and payload checks
                    |
               Redis / RQ
                    |
                  Worker
                    |
             Context Builder
                    |
               Orchestrator
          +---------+----------+
          |                    |
   curated RAG/Qdrant     Tavily fallback
          +---------+----------+
                    |
       OpenAgentic/Groq provider chain
                    |
        GitHub status, comment, or issue
```

## Components

| Area | Responsibility |
|---|---|
| `src/api` | FastAPI application, GitHub webhook, and Sentry webhook |
| `src/worker` | Redis connection, RQ jobs, and output coordination |
| `src/github` | GitHub HTTP client, repository policy, and shared connection pooling |
| `src/context` | File filtering, GitHub content retrieval, and related-file discovery |
| `src/orchestration` | Prompt construction, enrichment, provider fallback, and schema validation |
| `src/rag` | Optional curated retrieval plus local indexing and synchronization tools |
| `src/agents` | Sentry signature verification and payload parsing |
| `src/utils` | GitHub output formatting |

## GitHub review path

1. Verify the GitHub HMAC signature and repository allowlist.
2. Extract changed files and enqueue an RQ job.
3. Fetch analyzable and related files from GitHub.
4. Try curated RAG first; use Tavily when RAG is empty or unavailable.
5. Run the LLM provider fallback chain.
6. Publish a commit status and PR comment, or create an issue when no PR exists.

## Sentry path

1. Verify `Sentry-Hook-Signature` against the raw body.
2. Parse the supported issue, error, or event-alert payload.
3. Deduplicate the Sentry issue in Redis and enqueue an RQ job.
4. Fetch stack-trace context from the configured repository.
5. Validate structured LLM output against `BugAnalysis`.
6. Create an analyzed GitHub issue or a manual-review fallback issue.

## Failure boundaries

- Invalid signatures and unauthorized repositories fail before GitHub jobs are accepted.
- GitHub and Redis transient failures use bounded retry with exponential backoff.
- RAG and Tavily are enrichment only; their failure does not stop core analysis.
- LLM providers fail over in order. Total failure returns a controlled fallback.
- Source changes are never committed automatically; GitHub output remains human-reviewed.
