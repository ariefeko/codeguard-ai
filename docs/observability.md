# Observability

## Status and decision

LLM observability is planned but not implemented yet. Langfuse is the selected platform for LLM-specific tracing, evaluation, token usage, and cost visibility. OpenTelemetry remains the preferred companion for general application telemetry and reduced vendor coupling.

The initial deployment should use Langfuse Cloud Hobby for both development and low-volume production. Use separate Langfuse projects and API keys for each environment. Self-hosting remains an option when privacy, compliance, scale, or retention requirements justify the additional infrastructure and maintenance.

## Runtime model

Langfuse does not execute CodeGuard jobs or LLM requests. CodeGuard continues to run its API and RQ worker independently. Instrumentation in the worker sends sanitized telemetry asynchronously to Langfuse.

```text
GitHub or Sentry
        |
  CodeGuard API
        |
    Redis / RQ
        |
 CodeGuard Worker ----------------------+
        |                               |
 RAG / Tavily / LLM providers     sanitized telemetry
        |                               |
 GitHub status, comment, or issue       v
                                  Langfuse Cloud
                                        |
                                  trace dashboard
```

Observability must be non-blocking. A Langfuse outage, timeout, or export failure must not fail or delay the underlying CodeGuard review.

## Instrumentation boundaries

Create one root trace for each asynchronous job:

- `github-review` for `process_github_review()`.
- `sentry-analysis` for `process_sentry_job()`.

Add child observations for the meaningful stages of the job:

```text
github-review [trace]
├── repository-context [span]
├── rag-retrieval [span]
├── tavily-fallback [span, when used]
├── provider-attempt [generation, one per LLM attempt]
├── schema-validation [span, when applicable]
└── publish-github-result [span]
```

The first implementation milestone should instrument the two worker job boundaries and every LLM provider attempt in `src/orchestration/orchestrator.py`. Retrieval, validation, and GitHub publishing spans can follow once the minimal trace is stable.

## Trace metadata

Record metadata that helps correlate and compare jobs without exposing repository content:

- RQ job ID and a generated trace ID.
- Environment: `development`, `staging`, or `production`.
- Workflow: GitHub review or Sentry analysis.
- LLM provider, model, prompt version, attempt number, and fallback status.
- Repository identifier after sanitization, commit SHA, and language.
- Success or failure category, latency, token usage, and estimated cost.
- Whether RAG or Tavily enrichment was used.

Do not use a raw GitHub token, API key, email address, or other sensitive value as a trace or user identifier.

## Data protection

CodeGuard handles private source code and error context. Telemetry must pass through a shared redaction layer before export.

Do not export by default:

- API keys, access tokens, webhook secrets, or environment variables.
- Full source files, diffs, prompts, or model responses.
- Raw Sentry payloads, stack traces, or error messages.
- Personally identifiable information.
- Private repository paths or identifiers that have not been sanitized.

Prefer counts, hashes, categories, bounded excerpts, and allowlisted metadata. Prompt and response capture must be disabled initially and enabled only after explicit redaction tests cover the relevant payloads.

Use separate Langfuse projects and keys for development and production. Configure production sampling and review retention requirements before increasing traffic.

## Deployment choice

### Recommended initial setup

Run CodeGuard on Railway and send telemetry to Langfuse Cloud:

```text
Local CodeGuard  -> Langfuse project: codeguard-dev
Railway worker   -> Langfuse project: codeguard-prod
```

Store the Langfuse public key, secret key, and base URL as environment-specific secrets. Never commit them to the repository. Disabling or omitting the configuration must leave CodeGuard fully functional.

Langfuse Cloud Hobby can accept live production telemetry for an MVP. At the time of this decision it includes 50,000 billable units per month, 30 days of data access, two users, and no service-level agreement. One unit is one trace, observation, or score, so a CodeGuard job with one trace and five child observations consumes approximately six units. Confirm current limits on the [Langfuse Cloud pricing page](https://langfuse.com/pricing) before activation.

### Local or self-hosted setup

Langfuse can be run locally or on a VM with Docker Compose. The open-source core has no usage-based Langfuse fee, but the operator pays for and maintains the web and worker services, PostgreSQL, ClickHouse, Redis/Valkey, object storage, backups, upgrades, and availability.

Local self-hosting is useful for evaluation or strict data isolation. It is not the recommended first production deployment for CodeGuard because it adds substantial operational overhead. See the official [Docker Compose deployment guide](https://langfuse.com/self-hosting/deployment/docker-compose).

## Rollout plan

1. Add the Langfuse SDK and optional environment configuration.
2. Implement and test the shared telemetry redaction layer.
3. Trace the GitHub and Sentry RQ job boundaries.
4. Record each LLM provider attempt as a generation without raw input or output.
5. Verify that unavailable or invalid Langfuse configuration never fails a job.
6. Add retrieval, validation, and GitHub publishing spans.
7. Activate `codeguard-dev`, inspect representative traces, and validate redaction.
8. Activate sampled `codeguard-prod` telemetry on Railway.
9. Monitor monthly units, export failures, latency overhead, and data exposure.

## Acceptance criteria

- A GitHub review and a Sentry analysis each produce a correlated end-to-end trace.
- Provider fallback attempts and their latency are visible independently.
- Token and cost data are recorded when providers return usage metadata.
- No secret, full source content, raw Sentry payload, or unredacted private path is exported.
- CodeGuard jobs succeed when Langfuse is disabled or unavailable.
- Development and production traces cannot mix accidentally.

