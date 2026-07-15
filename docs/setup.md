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

Use `.env.example` as the configuration template. Keep production values in the
deployment platform rather than copying the local `.env` file to a server.

## Required environment

| Variable | Purpose |
|---|---|
| `GITHUB_PAT_TOKEN` | Read repository content and publish GitHub output |
| `GITHUB_WEBHOOK_SECRET` | Verify GitHub webhook signatures |
| `CODEGUARD_ALLOWED_REPOS` | Comma-separated `owner/repo` authorization list |
| `REDIS_URL` | Redis/RQ connection |
| `OPENAGENTIC_API_KEY` or `GROQ_API_KEY` | LLM provider access |

The runtime provider order is the two configured OpenAgentic models followed by
Groq. At least one corresponding API key must be available; configuring both
allows provider fallback.

### Sentry workflow

Sentry processing additionally requires:

| Variable | Purpose |
|---|---|
| `SENTRY_CLIENT_SECRET` | Verify Sentry webhook signatures |
| `CODEGUARD_DEFAULT_OWNER` | Target GitHub owner for Sentry events |
| `CODEGUARD_DEFAULT_REPO` | Target GitHub repository for Sentry events |
| `CODEGUARD_DEFAULT_BRANCH` | Optional branch override; otherwise use repository metadata, then `main` |
| `SENTRY_DEDUP_PENDING_TTL_SECONDS` | Pending deduplication lock duration; default `60` seconds |

The configured default owner/repository pair is treated as a single-repository
allowlist fallback. Webhook-side Sentry allowlist enforcement is still pending,
but `GitHubClient` enforces this policy when the worker accesses the repository.

### Webhook security

| Variable | Default | Purpose |
|---|---:|---|
| `WEBHOOK_MAX_BODY_SIZE_BYTES` | `10000000` | Maximum request body for either webhook |
| `WEBHOOK_RATE_LIMIT` | `10` | Requests allowed per client address and process window |
| `WEBHOOK_RATE_LIMIT_WINDOW_SECONDS` | `60` | In-process rate-limit window |

The rate limiter is process-local. Multiple API replicas do not share counters,
so deployment-level rate limiting is still recommended.

### Redis and RQ

| Variable | Default | Purpose |
|---|---:|---|
| `REDIS_URL` | none | Preferred Redis connection URL; supports `redis://`, `rediss://`, or `unix://` |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` | `5` | Redis connection timeout |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `5` | Redis operation timeout |
| `REDIS_RETRY_ATTEMPTS` | `3` | Retries for transient Redis failures |
| `REDIS_RETRY_BASE_DELAY_SECONDS` | `0.1` | Exponential retry base delay |
| `REDIS_RETRY_MAX_DELAY_SECONDS` | `2` | Maximum Redis retry delay |
| `RQ_JOB_TIMEOUT_SECONDS` | `120` | Maximum execution time assigned to queued jobs |

Railway-style `REDIS_PRIVATE_URL` and `REDIS_PUBLIC_URL` are accepted when
`REDIS_URL` is absent. As a final fallback, the runtime can construct a URL from
`REDISHOST`, `REDISPORT`, and `REDISPASSWORD`; the underscored forms
`REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` are also supported.

### HTTP and GitHub tuning

| Variable | Default | Purpose |
|---|---:|---|
| `HTTP_REQUEST_TIMEOUT_SECONDS` | `10` | Timeout for GitHub and other standard HTTP operations |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `60` | Timeout for each LLM provider request |
| `GITHUB_RETRY_MAX_ATTEMPTS` | `3` | Maximum GitHub API attempts |
| `GITHUB_RETRY_BASE_DELAY_SECONDS` | `0.5` | GitHub exponential retry base delay |
| `GITHUB_RETRY_MAX_DELAY_SECONDS` | `4` | Maximum GitHub retry delay |
| `CODEGUARD_STATUS_TARGET_URL` | empty | Optional link attached to GitHub commit statuses |

Invalid or non-positive numeric tuning values fall back to the code defaults
where they are read through `src/config.py`.

### Enrichment and RAG

| Variable | Default | Purpose |
|---|---:|---|
| `TAVILY_API_KEY` | empty | Optional current best-practice search when RAG has no result |
| `RAG_ENABLED` | `false` | Enable curated Qdrant retrieval |
| `QDRANT_URL` | empty | Qdrant endpoint |
| `QDRANT_API_KEY` | empty | Qdrant credential; optional only for trusted local instances |
| `RAG_MAX_RESULTS` | `5` | Maximum retrieved snippets |
| `RAG_MIN_CONFIDENCE` | `0.65` | Minimum accepted similarity score |
| `QDRANT_SYNC_TIMEOUT_SECONDS` | `30` | Timeout for Qdrant synchronization operations |

See [Curated RAG](rag.md) before enabling retrieval or running a remote sync.

### Diagnostics and reserved entries

| Variable | Default | Purpose |
|---|---:|---|
| `DEBUG_LLM_OUTPUT` | `0` | When `1`, include result length in completion metadata; raw LLM output is never logged |

`GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, and `WEBHOOK_URL`
are retained as local/reserved entries in `.env.example`, but the current
runtime does not consume them. Setting them does not add those providers to the
LLM fallback chain.

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
