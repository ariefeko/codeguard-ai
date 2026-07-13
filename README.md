# CodeGuard AI

CodeGuard is an asynchronous AI code-review service. It receives GitHub and Sentry webhooks, collects repository context, enriches analysis with optional curated RAG or Tavily, and publishes human-reviewable results to GitHub.

[![Status](https://img.shields.io/badge/status-active%20development-orange?style=flat-square)](PROGRESS.md)
[![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-198%20passed-brightgreen?style=flat-square)](tests)

## Capabilities

- Review GitHub push and pull-request changes.
- Convert Sentry errors into structured GitHub issues.
- Publish commit statuses, PR comments, and fallback issues.
- Use a bounded LLM provider fallback chain through OpenAgentic and Groq.
- Retrieve curated Qdrant knowledge when `RAG_ENABLED=true`.
- Fall back from RAG to Tavily, then to repository context alone.
- Keep all generated changes behind a human review gate.

## How it works

```text
GitHub or Sentry webhook
        |
 signature, payload, and repository checks
        |
     Redis / RQ
        |
       Worker
        |
 GitHub context + optional RAG/Tavily
        |
 OpenAgentic/Groq fallback
        |
 GitHub status, PR comment, or issue
```

See [Architecture](docs/architecture.md) for the detailed flows and failure boundaries.

## Quick start

Requirements: Python 3.11+, Redis, a GitHub fine-grained PAT, and at least one supported LLM provider key.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Minimum environment:

```text
GITHUB_PAT_TOKEN=...
GITHUB_WEBHOOK_SECRET=...
CODEGUARD_ALLOWED_REPOS=owner/repository
REDIS_URL=redis://...
OPENAGENTIC_API_KEY=...
# or GROQ_API_KEY=...
```

Run the API and worker in separate terminals:

```bash
uvicorn src.api.main:app --reload --port 8000
python -m src.worker.worker
```

Run tests:

```bash
.venv/bin/pytest -q
```

The latest verified result is **198 passed**. External GitHub, Sentry, Railway, and Qdrant checks remain operational tests.

## Webhooks

Configure GitHub to send JSON push and pull-request events to:

```text
POST https://your-codeguard-host/webhook/github
```

Use the same `GITHUB_WEBHOOK_SECRET` in GitHub and CodeGuard. The fine-grained PAT needs read-only Contents plus read/write Pull requests, Issues, and Commit statuses for allowed repositories.

Sentry events use:

```text
POST https://your-codeguard-host/webhook/sentry
```

Set `SENTRY_CLIENT_SECRET`, `CODEGUARD_DEFAULT_OWNER`, and `CODEGUARD_DEFAULT_REPO` for this flow.

Full environment and deployment instructions are in [Setup and Operations](docs/setup.md).

## Optional RAG

```text
RAG_ENABLED=true
QDRANT_URL=https://your-cluster
QDRANT_API_KEY=...
RAG_MAX_RESULTS=5
RAG_MIN_CONFIDENCE=0.65
```

RAG is read-only in the production request path and never blocks analysis. Indexing, synchronization, and update commands are documented in [Curated RAG](docs/rag.md).

## Project layout

```text
src/
├── agents/          Sentry verification and parsing
├── api/             FastAPI application and webhooks
├── context/         Repository context construction
├── github/          GitHub client and repository policy
├── orchestration/   Prompts, schemas, search, and LLM fallback
├── rag/             Curated retrieval and local index tooling
├── utils/           GitHub output formatting
└── worker/          Redis/RQ jobs

tests/               Mock-based automated tests
docs/                Current technical documentation
Documentation/       Legacy exports and design assets
```

## Documentation

- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Setup and Operations](docs/setup.md)
- [Curated RAG](docs/rag.md)
- [Security](docs/security.md)
- [Implementation progress](PROGRESS.md)

## Status

The GitHub and Sentry MVP workflows are implemented. Local RAG indexing and safe sync tooling are complete. The remaining work is primarily cloud/E2E validation, security hardening listed in [Security](docs/security.md), and incremental module cleanup.

## License

MIT
