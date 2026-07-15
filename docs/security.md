# Security

## Trust boundaries

CodeGuard accepts untrusted webhook payloads, retrieves private repository
content from GitHub, and sends selected source context to external LLM
providers. Redis/RQ separates request acceptance from analysis, but queued job
arguments and metadata must still be treated as sensitive application data.

The main boundaries are:

```text
GitHub / Sentry -> FastAPI webhooks -> Redis / RQ -> worker
                                               |-> GitHub API
                                               |-> Qdrant / Tavily
                                               `-> external LLM providers
```

Environment variables and deployment configuration are trusted operator input.
Webhook fields, GitHub API responses, Sentry payloads, repository paths, source
content, search results, and model responses are untrusted data.

## Implemented controls

### Webhook ingress

- GitHub requests require an HMAC SHA-256 signature over the raw body and use
  constant-time comparison.
- Sentry requests require an HMAC SHA-256 signature over the raw body. The
  implementation normalizes the candidate to a fixed length before
  constant-time comparison.
- Missing webhook secrets fail closed rather than accepting unsigned requests.
- Invalid JSON, unsupported Sentry resources, incomplete Sentry error data, and
  GitHub payloads without repository identity are rejected or safely skipped
  before enqueueing.
- Both webhook endpoints have a configurable request-body limit. The middleware
  checks declared content length and counts streamed chunks, returning `413`
  when the limit is exceeded.
- Both webhook endpoints have a configurable, per-client, per-process rate
  limit and return `429` with `Retry-After` when the limit is exceeded.
- Sentry issue IDs use an atomic Redis `SET ... NX` pending lock before
  enqueueing. Successful jobs receive a 24-hour deduplication marker, and a
  failed enqueue clears the pending lock.

### Repository and GitHub access

- The GitHub webhook enforces a normalized repository allowlist before file
  discovery or enqueueing. A missing allowlist fails closed.
- `GitHubClient` independently validates repository owner/name syntax and
  enforces the allowlist at the worker boundary.
- The context builder accepts repository-relative paths only. It rejects empty
  and null-byte paths, absolute POSIX/Windows/UNC paths, drive-qualified paths,
  traversal, and single- or double-encoded traversal.
- File analysis is restricted to supported extensions and skips configured
  generated, dependency, cache, and migration paths.
- Pull-request file discovery is bounded to 100 files per page and ten pages.
- GitHub output methods validate commit-status state and bound pull-request
  numbers before constructing output URLs.
- GitHub API calls use explicit timeouts and bounded retry for rate limits,
  transient transport failures, and server errors. Failure logs record
  categories and status metadata rather than response bodies.
- `GitHubClient` handles malformed or unexpected JSON response shapes for its
  repository, pull-request lookup, and issue operations.

### External services and model output

- LLM provider endpoints are fixed in code rather than derived from webhook
  input, TLS verification is enabled, and every request has a timeout.
- Provider errors and response parsing failures are logged using status,
  response size, or exception class; raw provider responses are not logged.
- Structured Sentry analysis is accepted only after validation against
  `BugAnalysis`. Invalid model output advances to the next provider.
- Runtime Qdrant access is read-only. Index and synchronization commands do not
  write remotely unless explicitly run with `--execute`.
- RAG and Tavily are optional enrichment. Their failure cannot bypass webhook
  validation or cause CodeGuard to execute generated source changes.
- CodeGuard publishes statuses, comments, and issues only. It never commits or
  applies model-generated source changes automatically.

### Logging and error handling

- Routine webhook and worker event logs use counts, event categories, job IDs,
  and status metadata instead of raw source, repository identity, Sentry
  messages, or repository paths.
- LLM completion logs never contain model output. `DEBUG_LLM_OUTPUT=1` adds only
  result length metadata.
- Redis configuration errors use fixed messages and never include connection
  URLs or passwords.
- Public webhook errors use a small response envelope and do not return internal
  exceptions or credentials.

## Remaining hardening

1. Enforce `is_repo_allowed()` in the Sentry webhook after resolving the
   configured repository and before Redis deduplication or enqueueing. Do not
   rely only on worker-side `GitHubClient` validation.
2. Validate the pull-request number and the type/shape of GitHub's changed-file
   response inside `extract_changed_files()` before iterating over it. The
   hardened parsing in `GitHubClient` does not currently cover this direct API
   call from the webhook.
3. Replace broad exception catches with expected exception types where
   practical. Retain a broad catch only at a safe process boundary that logs
   sanitized metadata and preserves the failure state.
4. Add deployment-level body limits and distributed rate limiting. The current
   limiter is in memory per API process, depends on the observed client address,
   and is not shared by multiple replicas.
5. Add an explicit retention and access policy for Redis/RQ job data.
   Repository identifiers, refs, file paths, and Sentry error fields cross the
   queue boundary even though they are excluded from application logs.
6. Implement and test the shared telemetry redaction layer before enabling
   Langfuse or any other observability exporter. Prompt and response capture
   must remain disabled until redaction is verified.

## Secret and data handling

- Never commit `.env`, API keys, webhook secrets, Redis credentials, private
  source, or production webhook payloads. Use `.env.example` only as a template.
- Store production secrets in the deployment platform and scope the GitHub PAT
  to the minimum repositories and permissions required.
- Treat prompts as disclosure of repository and Sentry context to the selected
  LLM provider. Confirm provider retention and training policies before sending
  private production code.
- Use TLS-enabled Redis and Qdrant endpoints for traffic that crosses a trusted
  private network boundary.
- Do not log exception strings, URLs, request/response bodies, stack traces, or
  environment values when they may contain source, credentials, or PII.
- Keep Tavily queries and RAG metadata free of raw secrets and unnecessary
  private identifiers.

Security findings should identify the entry point, threat scenario, affected
trust boundary, expected impact, mitigation, and a regression test whenever the
behavior is testable.
