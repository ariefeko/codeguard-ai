# Setup and Operations

## Requirements

- Python 3.11 or newer
- Redis
- GitHub fine-grained PAT
- At least one configured LLM provider
- Optional: Tavily and Qdrant Cloud

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` without committing secrets.

## Required environment

| Variable | Purpose |
|---|---|
| `GITHUB_PAT_TOKEN` | Read repository content and publish GitHub output |
| `GITHUB_WEBHOOK_SECRET` | Verify GitHub webhook signatures |
| `CODEGUARD_ALLOWED_REPOS` | Comma-separated `owner/repo` authorization list |
| `REDIS_URL` | Redis/RQ connection |
| `OPENAGENTIC_API_KEY` or `GROQ_API_KEY` | LLM provider access |

Sentry additionally requires:

| Variable | Purpose |
|---|---|
| `SENTRY_CLIENT_SECRET` | Verify Sentry webhook signatures |
| `CODEGUARD_DEFAULT_OWNER` | Target GitHub owner for Sentry events |
| `CODEGUARD_DEFAULT_REPO` | Target GitHub repository for Sentry events |
| `CODEGUARD_DEFAULT_BRANCH` | Optional branch override; defaults to repository metadata |

Optional enrichment uses `TAVILY_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, and the RAG variables documented in [RAG](rag.md).

## Run locally

Start Redis, then run the API and worker in separate terminals:

```bash
uvicorn src.api.main:app --reload --port 8000
python -m src.worker.worker
```

Health endpoint:

```text
GET /health
```

Webhook endpoints:

```text
POST /webhook/github
POST /webhook/sentry
```

GitHub should send push and pull-request events with JSON content and `X-Hub-Signature-256`. Sentry should send issue/error events with `Sentry-Hook-Signature`.

## Tests

All unit tests mock external services:

```bash
.venv/bin/pytest -q
```

Manual webhook scripts are in `tests/manual/` and are excluded from normal pytest discovery.

## Deployment

The repository includes:

- `Dockerfile` for the API container.
- `Procfile` commands for `web` and `worker` processes.
- `docker-compose.yml` for local Qdrant development.

Railway requires separate web and worker services sharing the same Redis configuration. Configure secrets in the deployment platform, never in tracked files.
