# CodeGuard AI

CodeGuard is an asynchronous AI code-review service. It receives GitHub and Sentry webhooks, collects repository context, enriches analysis with optional curated RAG or Tavily, and publishes human-reviewable results to GitHub.

[![Status](https://img.shields.io/badge/status-active%20development-orange?style=flat-square)](PROGRESS.md)
[![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-209%20passed-brightgreen?style=flat-square)](tests)

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

The latest verified result is **209 passed**. External GitHub, Sentry, Railway, and Qdrant checks remain operational tests.

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

## Deployment profiles

CodeGuard supports two deployment profiles without changing application code:

| Profile | Runtime | Public ingress | Intended use |
|---|---|---|---|
| Local | Docker Compose on the host | Tailscale Funnel | Free portfolio and low-volume use |
| Railway | Separate web, worker, and Redis services | Railway HTTPS hostname | Paid, continuously hosted deployment |

The `Dockerfile` and `Procfile` remain the Railway deployment configuration.
Local Compose overrides only the container `REDIS_URL`, so Railway variables can
remain stored in the Railway project while local secrets stay in the untracked
`.env` file.

### Run locally with Tailscale Funnel

Requirements: Docker with Compose, a computer that remains powered on, and a
Tailscale Personal account. No purchased domain or public IP is required.

Create and fill the local environment file once:

```bash
cp .env.example .env
```

Build and start FastAPI, the RQ worker, and persistent Redis:

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/health
```

On Ubuntu 24.04, install and authenticate Tailscale once from an interactive
terminal:

```bash
bash scripts/install_tailscale_ubuntu.sh
```

Publish only the loopback-bound FastAPI port and inspect the assigned public
hostname:

```bash
sudo tailscale funnel --bg 8000
tailscale funnel status
```

Configure the resulting `*.ts.net` hostname as follows:

```text
GitHub: https://your-hostname.ts.net/webhook/github
Sentry: https://your-hostname.ts.net/webhook/sentry
Health: https://your-hostname.ts.net/health
```

Redis, the worker, and optional Qdrant service are not exposed by Funnel. The
host must remain powered on, awake, connected to the internet, and running both
Docker and Tailscale; otherwise webhook delivery will fail.

### Manage the local deployment

```bash
# Service and public-ingress status
docker compose ps
tailscale funnel status

# Recent application and worker logs
docker compose logs --tail=100 app worker

# Follow logs
docker compose logs -f app worker

# Restart application services
docker compose restart app worker

# Rebuild after a code change
docker compose up -d --build

# Reload values after changing .env
docker compose up -d --build --force-recreate

# Stop public access first, then stop the stack
sudo tailscale funnel reset
docker compose down
```

`docker compose down` preserves the Redis queue and deduplication volume. Avoid
`docker compose down -v` unless deleting that state is intentional. The full
local runbook, including optional local Qdrant and recovery after reboot, is in
[Local Tailscale Deployment](docs/local-tailscale.md).

### Switch between local and Railway

Only one deployment should be the active webhook target. Local and Railway use
different Redis instances, so queued or running jobs are not migrated.

To switch from local to Railway:

1. Stop generating new webhook events and allow local jobs to finish.
2. Activate the Railway web, worker, and Redis services and verify Railway's
   `/health` endpoint.
3. Change both GitHub and Sentry webhook URLs to the Railway hostname.
4. Send a signed test event and confirm the expected GitHub result.
5. Run `sudo tailscale funnel reset`, then `docker compose down`.

To switch from Railway to local:

1. Stop generating new webhook events and allow Railway jobs to finish.
2. Start Compose and Funnel, then verify the public `*.ts.net/health` endpoint.
3. Change both webhook URLs to the Tailscale hostname.
4. Send a signed test event and confirm the expected GitHub result.
5. Stop the Railway services after the local flow is verified.

Always perform the health check and webhook cutover before stopping the old
deployment. Secrets stay in each deployment environment; never copy them into
tracked files.

## Optional RAG

```text
RAG_ENABLED=true
QDRANT_URL=https://your-cluster
QDRANT_API_KEY=...
RAG_MAX_RESULTS=5
RAG_MIN_CONFIDENCE=0.65
```

RAG is read-only in the production request path and never blocks analysis. Indexing, synchronization, and update commands are documented in [Curated RAG](docs/rag.md).

## Planned observability

Langfuse is the selected platform for LLM traces, provider fallback visibility, token usage, cost tracking, and later evaluation. Instrumentation is not implemented yet. The planned integration is asynchronous and non-blocking, with telemetry redaction and separate development and production projects.

See [Observability](docs/observability.md) for the architecture, Langfuse Cloud versus self-hosted decision, free-tier considerations, data policy, and rollout plan.

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
└── worker/          RQ jobs and Redis infrastructure

tests/               Mock-based automated tests
docs/                Current technical documentation
Documentation/       Legacy exports and design assets
```

## Documentation

- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Setup and Operations](docs/setup.md)
- [Local Tailscale Deployment](docs/local-tailscale.md)
- [Curated RAG](docs/rag.md)
- [Observability](docs/observability.md)
- [Security](docs/security.md)
- [Implementation progress](PROGRESS.md)

## Status

The GitHub and Sentry MVP workflows are implemented. Local RAG indexing and safe sync tooling are complete. The remaining work is primarily cloud/E2E validation, security hardening listed in [Security](docs/security.md), and incremental module cleanup.

## License

MIT
