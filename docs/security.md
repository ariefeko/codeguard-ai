# Security

## Implemented controls

- GitHub webhook HMAC SHA-256 verification.
- Sentry HMAC SHA-256 verification with normalized constant-time comparison.
- Repository allowlist enforcement in the GitHub webhook and `GitHubClient`.
- Repository owner/name and PR number validation.
- Repository path validation against absolute paths and traversal.
- Bounded GitHub PR file pagination.
- Malformed GitHub JSON response handling.
- TLS verification for external LLM providers.
- Bounded GitHub and Redis retry for transient failures.
- Sanitized Redis configuration errors.
- LLM results are not written to stdout; completion logs contain metadata only.

## Remaining hardening

1. Enforce `is_repo_allowed()` in the Sentry webhook before Redis operations and enqueueing, rather than relying only on worker-side `GitHubClient` validation.
2. Remove or sanitize logs containing Sentry error messages, repository paths, changed-file names, and analyzable-file names.
3. Replace broad exception catches with expected exception types where practical. Keep a broad catch only at a process boundary when it logs safely and re-raises.
4. Move the Sentry example payload from `src/api` to a test fixture before packaging production artifacts.

## Secret handling

- Never commit `.env`, API keys, webhook secrets, Redis credentials, or production payloads.
- Use fine-grained GitHub tokens with repository-specific access.
- Store production secrets in the deployment platform.
- Avoid logging exception strings when they may contain URLs, payloads, source code, or credentials.

Security findings should include an entry point, threat scenario, expected impact, and a regression test whenever the behavior is testable.
