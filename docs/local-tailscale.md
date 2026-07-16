# Local Deployment with Tailscale Funnel

This profile runs CodeGuard on a local computer while Tailscale Funnel provides
a stable public HTTPS endpoint. It is intended for portfolio and low-volume
development use. Railway remains the production deployment target and its
`Dockerfile`, `Procfile`, and platform variables are unchanged.

## Runtime layout

```text
GitHub / Sentry
      |
public HTTPS hostname on *.ts.net
      |
Tailscale Funnel on the host
      |
127.0.0.1:8000
      |
Docker Compose
├── app       FastAPI webhook API
├── worker    RQ worker
└── redis     persistent queue and Sentry deduplication

Qdrant Cloud remains external. A local Qdrant profile is optional.
```

The API port binds only to host loopback. Redis and the worker have no published
ports, so Funnel exposes only FastAPI.

## Prerequisites

- A computer that can remain powered on and connected to the internet.
- Docker Engine with Docker Compose.
- Tailscale 1.38.3 or newer on the host.
- A Tailscale Personal account with MagicDNS and HTTPS enabled.
- The secrets and repository policy described in [Setup and Operations](setup.md).

On this Ubuntu 24.04 host, run the repository installer from an interactive
terminal so `sudo` can request the local administrator password:

```bash
bash scripts/install_tailscale_ubuntu.sh
```

The script downloads the official Noble signing key and repository definition,
verifies the signing-key fingerprint, installs the package with `apt`, enables
`tailscaled`, and runs `tailscale up`. For another operating system, use the
official Tailscale installation instructions and then authenticate the device:

```bash
sudo tailscale up
```

Do not expose Redis or Qdrant through Funnel.

## Configure CodeGuard

Create the untracked local environment file if it does not already exist:

```bash
cp .env.example .env
```

Fill in the real secrets in `.env`. Keep the file local and never commit it.
Compose overrides `REDIS_URL` inside the `app` and `worker` containers with
`redis://redis:6379/0`, so an old Railway Redis URL in `.env` is not used by the
local stack. `.dockerignore` also excludes `.env` from the Docker build context;
Compose injects its values only at container runtime.

For Qdrant Cloud, retain the existing values:

```text
RAG_ENABLED=true
QDRANT_URL=https://your-qdrant-cloud-cluster
QDRANT_API_KEY=your-key
```

Set `RAG_ENABLED=false` when curated retrieval is not needed.

## Start the local stack

Build and start FastAPI, the RQ worker, and persistent Redis:

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/health
```

Inspect application and worker logs without printing `.env`:

```bash
docker compose logs --tail=100 app worker
```

Redis uses append-only persistence in the named `redis_data` volume. Normal
`docker compose down` keeps that volume. Do not use `docker compose down -v`
unless deleting the queue and Sentry deduplication state is intentional.

### Optional local Qdrant

To use Qdrant locally, set this only in `.env`:

```text
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
```

Then start the additional profile:

```bash
docker compose --profile local-rag up -d --build
```

The local Qdrant port also binds only to host loopback.

## Publish FastAPI with Funnel

Start Funnel in the background after the local health check passes:

```bash
sudo tailscale funnel --bg 8000
tailscale funnel status
```

The status output provides a stable URL similar to:

```text
https://codeguard.example-tailnet.ts.net
```

Configure the integrations with the exact hostname shown locally:

```text
GitHub: https://codeguard.example-tailnet.ts.net/webhook/github
Sentry: https://codeguard.example-tailnet.ts.net/webhook/sentry
```

Use the same HMAC secrets configured in `.env`. Verify the public health endpoint
before sending a test webhook:

```bash
curl --fail https://codeguard.example-tailnet.ts.net/health
```

If Funnel presents every request to FastAPI through the same proxy address, the
two webhook endpoints share the in-process client rate limit. For low-volume
portfolio traffic the default is normally sufficient. If legitimate events
receive `429`, raise `WEBHOOK_RATE_LIMIT` conservatively and restart `app`.

## Availability and automatic recovery

The containers use `restart: unless-stopped`, and Redis data survives container
restarts. For continuous availability:

1. Enable Docker and `tailscaled` at system startup.
2. Disable automatic sleep and hibernation while hosting CodeGuard.
3. Start the Compose stack after boot if the Docker installation does not restore
   it automatically.
4. Check `docker compose ps` and `tailscale funnel status` after a host restart.
5. Keep the host patched and restrict access to `.env` and Docker.

There is no cloud SLA. A host shutdown, sleep, network outage, Docker failure, or
Tailscale outage makes the webhook endpoint unavailable.

## Stop local hosting

Stop public ingress first, then the application stack:

```bash
sudo tailscale funnel reset
docker compose down
```

This preserves the Redis and Qdrant named volumes.

## Switch back to Railway

Do not run both deployments as active webhook targets. Their Redis instances do
not share queue or deduplication state.

1. Stop sending new events and let local RQ jobs finish.
2. Confirm the Railway web, worker, and Redis services have the required platform
   variables.
3. Start Railway and verify its `/health` endpoint.
4. Change the GitHub and Sentry webhook URLs to the Railway hostname.
5. Send one signed test event and verify the expected GitHub output.
6. Reset Funnel and stop the local Compose stack.

When moving from Railway back to local hosting, perform the same sequence in the
opposite direction: drain Railway jobs, start and verify local services, update
both webhook URLs, test one event, and only then stop Railway.

Queued jobs are not migrated automatically between Redis instances.
