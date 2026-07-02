# CodeGuard AI - Test Snapshot

Snapshot date: 2026-06-27, Asia/Jakarta

Purpose: compact handoff context for Claude.ai about the tests already performed on CodeGuard AI.

## Current Automated Test Status

Command used:

```bash
.venv/bin/python -m pytest
```

Result:

```text
50 passed in 0.07s
```

Test environment:

```text
Python 3.12.3
pytest 8.2.2
pytest.ini testpaths = tests
manual tests excluded by norecursedirs = manual .git .venv
```

Note: `pytest` and `python -m pytest` were not available from the system Python, but the repository virtualenv `.venv` works.

## Automated Tests Covered

### GitHub Client

File: `tests/test_github_client.py`

Covered behavior:

- `create_issue()` returns `True` on HTTP 201.
- `create_issue()` returns `False` on HTTP errors.
- Default issue label is `["codeguard-ai"]`.
- Custom issue labels are passed through without being overwritten.
- Fallback/manual-review label usage is supported, including `needs-manual-review`.
- Network exceptions during issue creation return `False` instead of crashing.
- `post_pr_comment()` returns `True` on HTTP 201.
- `post_pr_comment()` returns `False` on HTTP errors.
- PR comment URL includes the correct PR number via `/issues/{pr_number}/comments`.
- Network exceptions during PR comment creation return `False`.
- `get_open_pr_for_branch()` returns the first open PR number when found.
- `get_open_pr_for_branch()` returns `None` when no PR exists.
- `get_open_pr_for_branch()` returns `None` on HTTP errors.
- `get_open_pr_for_branch()` returns the first PR when multiple PRs are returned.
- Exceptions while fetching PRs return `None`.

What this proves:

- GitHub output paths are defensive and do not crash on HTTP/network failures.
- PR comment and GitHub Issue fallback behavior can be tested without live GitHub calls.

### Orchestrator

File: `tests/test_orchestrator.py`

Covered behavior:

- `_call_llm()` returns the first successful provider response.
- `_call_llm()` falls back to the next provider when the first returns `None`.
- `_call_llm()` returns `"Error: all providers failed."` when every provider fails.
- `_call_llm_structured()` accepts the first schema-valid bug analysis response.
- `_call_llm_structured()` falls back after invalid JSON/schema output.
- `_call_llm_structured()` falls back after request failure.
- `_call_llm_structured()` returns `None` when every provider output is invalid.
- `review_code()` enriches context with Tavily search results, builds a review prompt, and calls the LLM.
- `fix_bug()` searches for error context, builds the bug-fix prompt, and requests structured output.
- `_enrich_with_search()` adds Python/FastAPI security references for `.py` files.
- `_enrich_with_search()` adds PHP/Laravel security, OWASP injection, and OWASP Top 10 references for `.php` files.
- `_enrich_with_search()` adds Node.js/JavaScript security references for `.js`/`.ts` files.
- `_search_for_error()` searches by error type when available.
- `_search_for_error()` skips search when no error type exists.
- `_request()` returns `None` when the provider API key is missing.
- `_request()` sends `response_format: {"type": "json_object"}` when JSON mode is enabled.
- `_request()` sends the configured `max_tokens`.
- `_request()` sends the expected OpenAI-compatible `messages` payload.
- `_request()` handles OpenAgentic's trailing `data: [DONE]` suffix.
- `_request()` returns `None` for provider HTTP errors.
- `_request()` returns `None` for malformed provider responses.
- `_request()` returns `None` for network exceptions.

What this proves:

- Multi-provider LLM fallback behavior is covered.
- Sentry bug analysis uses structured schema validation before accepting LLM output.
- Provider-specific response quirks are guarded, especially the OpenAgentic `data: [DONE]` suffix.
- Search enrichment is routed by changed-file language.

### Sentry Agent

File: `tests/test_sentry_agent.py`

Covered behavior:

- Valid HMAC SHA-256 Sentry signature is accepted.
- Invalid signature is rejected.
- Missing signature header is rejected.
- Missing `SENTRY_CLIENT_SECRET` rejects requests.
- Confirmed `data.issue.data.*` payloads are parsed into:
  - error type
  - message
  - affected file
  - affected line
  - related in-app file paths
  - issue id
- Metadata line number is preferred when available.
- Metadata file is used when stacktrace is missing.
- Exception stacktrace is used when metadata has no filename.
- Issue state changes without error detail return `None`.
- `data.error.*` resource payloads are parsed as fallback.
- Error resource without exception returns `None`.
- `data.event.*` resource payloads are parsed as fallback.
- Event payloads without stacktrace fall back to title and metadata.
- Event payloads without exception or title return `None`.
- Unknown payload structures return `None`.

What this proves:

- Sentry signature security is covered.
- Parser supports the confirmed Sentry Issue payload path plus fallback payload shapes.
- Non-error Sentry events are ignored safely.

## Manual / Integration Tests Already Documented

### GitHub Webhook End-to-End

Source: `PROGRESS.md`, Phase 8.

Confirmed historical flow:

```text
git push / open PR on Tagihin
  -> GitHub sends POST /webhook/github
  -> changed files extracted
  -> non-code files filtered
  -> ContextBuilder fetches changed files via GitHub API
  -> related files resolved via repo tree cache
  -> Orchestrator.review_code()
  -> prompt built with line numbers
  -> LLM provider called
  -> GitHubClient finds open PR
  -> PR comment posted
```

Observed/confirmed from the previous test:

- Changed files were extracted correctly.
- Related files were resolved, including `ProfileUpdateRequest.php` and `LoginRequest.php`.
- LLM detected real issues such as `dd()` statements, SQL injection, and hardcoded secrets.
- Reported line numbers were accurate after adding line numbers to prompts.
- PR comment was posted to GitHub.

### Sentry Webhook Local Manual Test

File: `tests/manual/test_sentry_webhook.py`

Manual test design:

- Runs against local FastAPI endpoint `http://localhost:8000/webhook/sentry`.
- Builds a fake Sentry Issue Alert payload for a Laravel/PHP error.
- Signs the raw JSON body with HMAC SHA-256 using `SENTRY_CLIENT_SECRET`.
- Sends the request with:
  - `Content-Type: application/json`
  - `Sentry-Hook-Signature`
  - `Sentry-Hook-Resource: issue`
- Expects valid signature path to return HTTP 202 and enqueue a job.
- Sends a second request with intentionally invalid signature.
- Expects invalid signature path to return HTTP 401.

Manual run prerequisites:

```bash
uvicorn src.api.main:app --reload
export $(cat .env | grep -v '^#' | xargs)
python tests/manual/test_sentry_webhook.py
```

Worker verification is manual:

```bash
rq worker
```

Expected worker-side result:

- Sentry job is enqueued.
- Worker processes bug analysis.
- GitHub Issue is created or fallback issue is created when AI analysis fails.

## Relevant Implementation Notes Tested

### Structured Bug Analysis Schema

File: `src/orchestration/schemas.py`

The Sentry `fix_bug()` path validates LLM output against `BugAnalysis`:

```text
status: COMPLETE | PARTIAL | INSUFFICIENT_DATA
root_cause
affected_file
affected_line
fix_steps
quick_fix_code
prevention
inferences[]
insufficient_data_reason
```

Important behavior:

- Invalid JSON or schema mismatch triggers fallback to the next provider.
- Markdown-fenced JSON is cleaned before validation.
- Structured mode is isolated to Sentry bug analysis; PR review still uses free-text output.

### Provider Chain

File: `src/orchestration/orchestrator.py`

Current provider chain:

```text
1. DeepSeek V4 Flash (OpenAgentic)
2. GLM-5 (OpenAgentic)
3. Llama 3.3 70B (Groq)
```

Tested fallback characteristics:

- Missing API key does not call the provider.
- HTTP errors fail closed and continue fallback.
- Malformed responses fail closed and continue fallback.
- Network exceptions fail closed and continue fallback.
- Structured responses must pass schema validation before being accepted.

### Sentry Payload Strategy

File: `src/agents/sentry_agent.py`

Parser priority:

```text
1. data.issue.data.*
2. data.error.*
3. data.event.*
4. issue state-change fallback -> ignore
5. unknown payload -> ignore
```

Confirmed payload path:

```text
data.issue.data.exception.values[].stacktrace.frames[]
data.issue.data.metadata
```

This was noted in code comments as confirmed from a real Tagihin `ModelNotFoundException` payload on 2026-06-20.

## Current Gaps / Not Yet Fully Automated

- FastAPI webhook endpoints are not yet covered by automated HTTP tests.
- Redis queue enqueue behavior is not yet unit/integration tested.
- `ContextBuilder` GitHub file fetching and dependency resolution are not currently covered by the visible automated tests.
- Worker behavior in `src/worker/worker.py` is not currently covered by visible automated tests.
- Manual Sentry webhook script exists, but this snapshot did not rerun it because it requires a running FastAPI server, environment secrets, and Redis/worker setup.
- Live GitHub PR comment posting was documented as historically confirmed in `PROGRESS.md`, but this snapshot did not re-run live GitHub integration.
- Tavily/live web search is mocked in unit tests; live Tavily integration was not re-run in this snapshot.

## Files Most Relevant For Claude.ai Review

Core runtime:

- `src/api/webhook.py`
- `src/worker/worker.py`
- `src/agents/sentry_agent.py`
- `src/context/context_builder.py`
- `src/orchestration/orchestrator.py`
- `src/orchestration/prompts.py`
- `src/orchestration/schemas.py`
- `src/github/github_client.py`

Tests:

- `tests/test_github_client.py`
- `tests/test_orchestrator.py`
- `tests/test_sentry_agent.py`
- `tests/conftest.py`
- `tests/manual/test_sentry_webhook.py`

Project docs:

- `README.md`
- `PROGRESS.md`

## Short Handoff Summary

CodeGuard AI currently has 50 passing automated tests covering the GitHub client, LLM orchestration/fallback, structured Sentry bug analysis, Tavily enrichment routing via mocks, Sentry signature verification, and robust Sentry payload parsing. The GitHub webhook to PR comment flow has been historically confirmed end-to-end against the Tagihin repo and documented in `PROGRESS.md`. Manual Sentry webhook testing exists with signed fake payloads and invalid-signature checks, but full live Sentry-to-worker-to-GitHub-Issue execution was not re-run in this snapshot.
