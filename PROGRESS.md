# CodeGuard AI — Implementation Progress

> Step-by-step engineering log. Updated per session.

---

## Project Structure (Current)

```text
codeguard-ai/
├── src/
│   ├── agents/
│   │   └── sentry_agent.py        # File scanner engine
│   ├── api/
│   │   ├── main.py                # FastAPI app entry point
│   │   └── webhook.py             # Webhook route handlers
│   ├── context/
│   │   └── context_builder.py     # GitHub API file fetcher + dependency resolver
│   ├── github/
│   │   └── github_client.py       # GitHub API wrapper (PR comment, Issue)
│   ├── orchestration/
│   │   ├── orchestrator.py        # LLM orchestration + fallback chain
│   │   └── prompts.py             # Prompt templates per analysis type
│   ├── rag/
│   │   └── __init__.py            # Placeholder — RAG pipeline (not yet implemented)
│   ├── utils/
│   │   └── __init__.py            # Placeholder
│   └── config.py                  # Single source of truth: extensions, skip dirs
├── tests/
│   ├── conftest.py
│   └── __init__.py
├── .env                           # API keys (not committed)
├── .env.example
├── .gitignore
├── .pylintrc
├── docker-compose.yml
├── Procfile                       # Railway deploy config
├── requirements.txt
└── README.md
```

---

## Phase 1 — Foundation

### Python Environment

- Python 3.12 + `.venv` initialized
- `requirements.txt` configured
- Git repository initialized

### Project Structure

- `src/`, `tests/`, `docs/` directories created
- `src/__init__.py` per module

### `src/config.py` — Single Source of Truth

Centralized constants used across all modules:

```python
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".php", ".java", ".go",
    ".cs", ".razor", ".cshtml"
}

SKIP_DIRS = {
    "node_modules", "vendor", "core", ".git",
    ".venv", "__pycache__", "dist", "build",
    "coverage", ".next", ".nuxt", "migrations",
    "storage", "bootstrap/cache"
}

SKIP_FILES = { "__init__.py", "conftest.py" }
```

Previously each module had its own duplicate constants (`IGNORE_DIRS`, `ANALYZABLE_EXTENSIONS`). Refactored to single import:

```python
from src.config import SUPPORTED_EXTENSIONS, SKIP_DIRS
```

---

## Phase 2 — Scanner Engine

### `src/agents/sentry_agent.py`

```text
SentryAgent(project_path)
  ├── collect_files()     → rglob project, filter by SUPPORTED_EXTENSIONS + SKIP_DIRS
  ├── read_file()         → read single file as string
  └── read_files()        → read all collected files, return dict {path: content}
```

Originally had local `IGNORE_DIRS` / `SUPPORTED_EXTENSIONS` — refactored to import from `src/config.py`.

`extract_dependencies()` and `find_related_files()` stubs present — dependency logic moved to `ContextBuilder`.

---

## Phase 3 — API Layer

### `src/api/main.py` — FastAPI Entry Point

```python
from dotenv import load_dotenv
load_dotenv()  # must be called before all other imports

app = FastAPI()
app.include_router(router)

@app.get("/health")
def health():
    return {"status": "ok"}
```

Key lesson: `load_dotenv()` must be at the top of `main.py` — not in `webhook.py` — otherwise environment variables are not available when FastAPI starts.

### `src/api/webhook.py` — Webhook Handlers

```text
POST /webhook/github  → handle push + pull_request events
POST /webhook/sentry  → receive Sentry error payload (print only, not yet processed)
```

---

## Phase 4 — GitHub Webhook Integration

### ngrok Setup

```bash
ngrok config add-authtoken YOUR_TOKEN
ngrok http 8000
# → https://xxxx.ngrok-free.app
```

Expose local FastAPI to the internet. URL changes on every restart — update GitHub webhook settings accordingly.

### tmux Workflow

```bash
tmux new -s codeguard

# Panel 1 (Ctrl+B %)
uvicorn src.api.main:app --reload --port 8000

# Panel 2
ngrok http 8000

# Detach
Ctrl+B D

# Reattach
tmux attach -t codeguard
```

### GitHub Webhook Connected

Configured on repo **Tagihin** (demo app — TALL stack Laravel):

```text
Payload URL : https://xxxx.ngrok-free.app/webhook/github
Content type: application/json
Events      : Pushes + Pull requests
```

### `extract_changed_files()` — Parse Push + PR Payload

**Push event** — files extracted from commit payload:

```python
commits = payload.get("commits", [])
for commit in commits:
    files.update(commit.get("added", []))
    files.update(commit.get("modified", []))
```

**Pull request event** — files fetched via GitHub API (not in payload directly):

```python
url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
for f in response.json():
    if f.get("status") != "removed":
        files.add(f["filename"])
```

### `extract_branch()` — Detect Branch from Payload

```python
# Push event
ref = payload.get("ref", "")  # "refs/heads/develop"
branch = ref.replace("refs/heads/", "")
 
# Pull request event
branch = payload["pull_request"]["head"]["ref"]
```

---

## Phase 5 — Context Builder

### `src/context/context_builder.py`

```text
ContextBuilder(owner, repo, ref)
  ├── build(changed_files)       → entry point, returns context dict
  ├── _filter(files)             → remove non-code files (.md, .blade.php, etc.)
  ├── _fetch_file(path)          → GitHub API GET /contents/{path}?ref={sha}
  ├── _fetch_files(files)        → fetch multiple files
  ├── _get_repo_tree()           → fetch full repo tree once, cache result
  ├── extract_dependencies()     → regex per language to find imports
  ├── find_related_files()       → resolve imports to actual file paths
  ├── _resolve_dep()             → language-specific resolution logic
  └── _search_file_in_tree()     → search cached tree by filename + extension
```

**Output format:**

```python
{
    "changed_files": {
        "app/Http/Controllers/ProfileController.php": "<?php ..."
    },
    "related_files": {
        "app/Http/Requests/ProfileUpdateRequest.php": "<?php ..."
    }
}
```

**Import patterns supported (regex per language):**

```text
PHP    → use App\Services\InvoiceService;
Python → from services.invoice import InvoiceService
JS/TS  → import { X } from './services/invoice'
Java   → import com.app.services.InvoiceService;
Go     → import "github.com/app/services"
C#     → using MyApp.Services;
Razor  → @using MyApp.Services / @inject
```

**GitHub API used:**

```text
GET /repos/{owner}/{repo}/contents/{path}?ref={sha}  → fetch file content (base64)
GET /repos/{owner}/{repo}/git/trees/{ref}?recursive=1 → fetch full repo tree (cached)
```

**Key design decision:** ContextBuilder fetches files from GitHub API, not local disk. This means CodeGuard AI works from anywhere — no need to clone the target repo.

---

## Phase 6 — Orchestration + LLM

### `src/orchestration/prompts.py`

Two prompt builders:

```python
build_code_review_prompt(context)  # GitHub webhook → code review
build_bug_fix_prompt(context, error)  # Sentry webhook → bug fix (not yet used)
```

**Line numbers added to file content:**

```python
def add_line_numbers(content: str) -> str:
    lines = content.splitlines()
    return "\n".join(f"{i:4d} | {line}" for i, line in enumerate(lines, start=1))
```

Without line numbers, LLM guesses positions inaccurately. Adding them ensures LLM reports exact line numbers matching the actual file.

### `src/orchestration/orchestrator.py`

```text
Orchestrator
  ├── review_code(context)   → build prompt → _call_llm()
  ├── fix_bug(context, error) → build prompt → _call_llm() (not yet wired)
  ├── _call_llm(prompt)      → iterate MODEL_CHAIN until success
  └── _request(prompt, model) → POST to OpenRouter API
```

**Fallback chain:**

```python
MODEL_CHAIN = [
    "deepseek/deepseek-v4-flash",
    "google/gemini-3-flash-preview",
    "meta-llama/llama-4-maverick:free",
    "qwen/qwen3.7-max",
    "deepseek/deepseek-v4-pro",
]
```

All models accessed via **OpenRouter** — single API key, automatic fallback if a model is unavailable or rate-limited.

---

## Phase 7 — GitHub Output

### `src/github/github_client.py`

```text
GitHubClient(owner, repo)
  ├── get_open_pr_for_branch(branch)  → GET /pulls?state=open&head={owner}:{branch}
  ├── post_pr_comment(pr_number, body) → POST /issues/{pr}/comments
  └── create_issue(title, body)        → POST /issues (fallback if no open PR)
```

**PAT token permissions required:**

```text
Contents      → Read-only   (fetch files)
Pull requests → Read and write  (post PR comment)
Issues        → Read and write  (create issue)
Metadata      → Read-only   (auto)
```

### `format_pr_comment()` — PR Comment Template

```python
def format_pr_comment(review_result: str) -> str:
    return f"""## 🤖 CodeGuard AI Review

{review_result}

---
*Generated by [CodeGuard AI](https://github.com/ariefeko/codeguard-ai)*"""
```

---

## Phase 8 — End-to-End Flow (Confirmed Working)

```text
git push / open PR (Tagihin repo)
  → GitHub sends POST to /webhook/github
    → extract_changed_files()
      → filter non-code files
        → ContextBuilder.build(changed_files)
          → _fetch_files() via GitHub API
          → find_related_files() via repo tree cache
            → Orchestrator.review_code(context)
              → build_code_review_prompt() with line numbers
                → OpenRouter → DeepSeek V4 Flash
                  → GitHubClient.get_open_pr_for_branch()
                    → post_pr_comment() ✅
                    → fallback: create_issue() if no open PR
```

**Verified with real push to Tagihin:**
- Changed files extracted correctly ✅
- Related files resolved (ProfileUpdateRequest.php, LoginRequest.php) ✅
- LLM detected real bugs (dd() statements, SQL injection, hardcoded secrets) ✅
- Line numbers accurate ✅
- PR comment posted to GitHub ✅

---

## Connect CodeGuard AI to Any Repo

Zero setup on the target repo. Two steps only:

**Step 1 — Add GitHub webhook on target repo:**

```text
Settings → Webhooks → Add webhook
Payload URL : https://your-codeguard-url/webhook/github
Content type: application/json
Events      : Pushes + Pull requests
```

**Step 2 — Grant PAT token access:**

```text
GitHub → Settings → Developer settings →
Personal access tokens → Fine-grained tokens →
codeguard-ai token → Edit →
Repository access → add target repo

Permissions: Contents (read), Pull requests (write), Issues (write)
```

CodeGuard AI reads all files via GitHub API — no cloning, no local access required.

---

## Environment Variables

```bash
# Required
GITHUB_PAT_TOKEN=github_pat_xxxx    # Fine-grained PAT
OPENROUTER_API_KEY=sk-or-xxxx       # openrouter.ai

# Optional (future)
# SENTRY_WEBHOOK_SECRET=xxxx
# REDIS_URL=xxxx
```

---

## Local Development

```bash
# Start FastAPI
uvicorn src.api.main:app --reload --port 8000

# Expose to internet (required for GitHub webhook)
ngrok http 8000

# Recommended: run both in tmux
tmux new -s codeguard
```

---

## Next Steps

### Roadmap (in order)

```text
⬜ Phase 9  — Railway Deploy
             Production hosting, public URL, no more ngrok
 
⬜ Phase 10 — Redis Queue Bridge
             Async processing, instant 202 response, worker pattern
 
⬜ Phase 11 — Sentry Webhook Integration
             Extract error context → wire to fix_bug()
             Output: GitHub Issue + Auto Fix PR (human-in-the-loop)
 
⬜ Phase 12 — Tavily Web Search Integration
             Real-time security advisories + best practices
             LLM findings backed by external sources
 
⬜ Phase 13 — pytest + Mock LLM
             Test suite without real API calls, no billing
 
⬜ Phase 14 — RAG Pipeline (Phase 2)
             LangChain + Qdrant + nomic-embed-text
             Internal knowledge base + organization coding standards
```

---

### Future Architecture (with Tavily + RAG)

```text
GitHub PR / Sentry Error
    ↓
FastAPI Webhook
    ↓
Redis Queue
    ↓
Worker
    ↓
Context Builder
    ↓
Orchestration
    ├── Local Context (changed files + related files)
    ├── Tavily Search (CVE, security advisories, best practices)
    └── RAG Knowledge Base (Phase 2 — LangChain + Qdrant)
    ↓
OpenRouter → LLM Fallback Chain
    ↓
Output
    ├── PR Comment (GitHub webhook)
    └── GitHub Issue + Auto Fix PR (Sentry webhook)
    ↓
Human Review Gate (approve / reject / modify)
```

---

### Why Tavily Before RAG?

Currently CodeGuard AI can only analyze:

```text
- Source code
- Changed files
- Related files
- Dependency relationships
```

With Tavily, CodeGuard AI can additionally search:

```text
- CVE databases
- Package vulnerabilities (Composer, npm, pip)
- Framework security advisories (Laravel, Symfony, Drupal)
- OWASP recommendations
- Latest best practices per language/framework
```

**Example:**

```text
Detected: symfony/http-foundation 6.4.x
 
Tavily searches:
  "symfony http-foundation 6.4 vulnerability"
  "symfony security advisory 2025"
 
Result: PR comment includes real CVE references,
not just LLM training data (which may be outdated)
```

RAG requires significantly more setup (embedding pipeline, vector DB, chunking strategy, retrieval tuning) — Tavily delivers immediate value in one API call.

---
