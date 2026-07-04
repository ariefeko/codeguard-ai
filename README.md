# 🛡️ CodeGuard AI

> **AI-powered autonomous code guardian.** Detects bugs, reviews PRs, and generates fix suggestions — for any language, any framework.

[![Status](https://img.shields.io/badge/status-active%20development-orange?style=flat-square)](https://github.com/ariefeko/codeguard-ai)
[![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

---

## What is CodeGuard AI?

CodeGuard AI is an **autonomous engineering system** that runs in the background — monitoring your codebase, detecting issues, and proposing fixes automatically. Not just a linter, but an agent that reasons.

**4 core capabilities:**

| Capability | Description |
|---|---|
| **Bug Detection** | Sentry error received → AI analyzes root cause + stack trace |
| **PR Review** | GitHub PR opened → AI reviews diff, flags issues, suggests improvements |
| **Security Scan** | Detects SQL injection, exposed secrets, insecure patterns |
| **Code Quality** | Best practice checks, scalability warnings, refactor suggestions |

**Human-in-the-loop:** AI proposes via PR comment or GitHub Issue — humans approve/merge. No automatic changes without review.

---

## Connect CodeGuard AI to Any Repo

CodeGuard AI requires **zero setup on the target repo** — no SDK, no config file, no dependency install. Just two steps:

### Step 1 — Add GitHub Webhook

Go to your target repo on GitHub:

```
Settings → Webhooks → Add webhook

Payload URL : https://your-codeguard-url/webhook/github
Content type: application/json
Secret      : (optional, for signature verification)
Events      : ✅ Pushes
              ✅ Pull requests
Active      : ✅
```

### Step 2 — Grant PAT Token Access

```
GitHub → Settings → Developer settings →
Personal access tokens → Fine-grained tokens →
codeguard-ai token → Edit →
Repository access → add your target repo

Permissions required:
  ✅ Contents      → Read-only
  ✅ Pull requests → Read and write
  ✅ Issues        → Read and write
  ✅ Metadata      → Read-only (auto)
```

That's it. CodeGuard AI reads all files via GitHub API — no cloning, no local access required.

> **Supports any language:** PHP, Python, JavaScript, TypeScript, Java, Go, C#, Razor, and more.

---

## System Architecture

```
Any Codebase (Any Language / Framework)
   │
   ├── (Trigger: Sentry Webhook / GitHub Actions PR / Scheduled Cron)
   ▼
Backend App (FastAPI Webhook Receiver)
   │
   ▼
Redis Queue (Async processing; returns an instant 202 response)
   │
   ▼
FastAPI Worker (Hosted on Railway)
   │
   ▼
Orchestration Layer (Python 3.11 + LangChain)
   ├── Retrieval-Augmented Generation (Optional: Qdrant + nomic-embed-text)
   └── Prompt Engineering & Context Assembly
   │
   ▼
OpenRouter LLM Gateway
   ├── Primary: DeepSeek V3 (Free Tier)
   ├── Fallback 1: Gemini Flash
   └── Fallback 2: Groq / Llama
   │
   ▼
Output Generation
   ├── GitHub PR Comment
   ├── GitHub Issue Creation
   └── Auto-fix Commit (Awaiting manual gate)
   │
   ▼
✅ Human Review Gate (Approve / Reject → Merge)
```

---

## Core Features

### Sentry Bug Agent
Production error received by Sentry → webhook trigger → AI analyzes within seconds → GitHub Issue created automatically with:
- Root cause analysis
- Files likely involved
- Step-by-step fix suggestion
- Quick fix for immediate production relief
- Prevention strategy going forward

### PR Auto-Review Agent
PR opened on GitHub → GitHub Actions trigger → AI reviews diff → comments directly on PR with:
- Bug detection per line
- Security vulnerability check
- Best practice violations
- Performance concerns
- Improvement suggestions

### Security Scanner
- SQL Injection pattern detection
- Exposed API keys / secrets
- Insecure direct object reference
- Missing authentication checks
- Dependency vulnerability hints

### Code Quality Analyzer
- SOLID principle violations
- N+1 query detection
- Scalability bottlenecks
- Dead code identification
- Refactor opportunities

---

## Tech Stack

```
Layer               Technology
─────────────────────────────────────────
AI Orchestration    Python 3.11 + LangChain
API Layer           FastAPI + Uvicorn
Queue Bridge        Redis (async job queue)
LLM Gateway         OpenRouter (multi-provider fallback)
LLM Primary         DeepSeek V3 (free tier)
LLM Fallback        Gemini Flash → Groq/Llama
Embeddings          nomic-embed-text (local, Ollama)
Vector Store        Qdrant (RAG, optional)
Deploy              Railway (free tier)
Monitoring          Sentry webhook receiver
CI/CD Trigger       GitHub Actions
Testing             pytest + mock LLM
```

---

## 📁 Project Structure

```
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
│   │   ├── qdrant_client.py       # Read-only Qdrant runtime client
│   │   ├── qdrant_smoke.py        # Qdrant Cloud smoke query command
│   │   ├── rag_pipeline.py        # Optional curated RAG retrieval
│   │   ├── topic_mapper.py        # Maps code/error context to RAG topics
│   │   └── seeds/                 # Curated MVP knowledge seed
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

## Quick Start

### Prerequisites
- Python 3.11+
<!-- - Redis (local or Railway) -->
- OpenRouter API key (free tier)
- GitHub Personal Access Token
<!-- - Sentry account (free tier) -->
- Railway account (free tier) ← used for deploy

### Setup

```bash
# Clone the repo
git clone https://github.com/ariefeko/codeguard-ai.git
cd codeguard-ai

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env
```

### Environment Variables

```bash
# LLM Providers (fallback chain)
GITHUB_PAT_TOKEN = your-github-pat-token-here
OPENROUTER_API_KEY = your-openrouter-api-key-here

# Optional RAG / Qdrant Cloud
QDRANT_URL = your-qdrant-cloud-url-here
QDRANT_API_KEY = your-qdrant-api-key-here
RAG_ENABLED = false
RAG_MAX_RESULTS = 5
RAG_MIN_CONFIDENCE = 0.65
```

### Qdrant Cloud Smoke Test

```bash
source .venv/bin/activate
RAG_ENABLED=true python -m src.rag.qdrant_smoke
```

If the command reports that Qdrant is connected but no collections are indexed
yet, the cloud wiring is valid and the next step is running the Phase 14.8
indexer/sync.

For a local Qdrant instance without an API key:

```bash
RAG_ENABLED=true python -m src.rag.qdrant_smoke --allow-missing-api-key
```

### Run Locally

```bash
source .venv/bin/activate

# Create new Session
tmux new -s codeguard

# Left panel: FastAPI
uvicorn src.api.main:app --reload --port 8000

# Create 2 panel inside tmux — Ctrl+B + %

# Right panel: ngrok (Ctrl+B + arrow left or right)
ngrok http 8000

# Detach
Ctrl+B lalu D

# open tmux session again:
tmux attach -t codeguard
```

<!-- ### Run Tests

```bash
# Run all tests (no real API calls — LLM is mocked)
pytest -v

# Run with coverage report
pytest --cov=src --cov-report=term-missing
``` -->

---

## LLM Fallback Chain

CodeGuard AI does not rely on a single provider. If one is down or rate-limited, the system automatically falls back:

```
Incoming Analysis Request
         │
         ▼
[ Attempt: DeepSeek V3 ] ──(Success)──→ Complete & Send Output
         │
     (Failed / Rate Limited)
         ▼
[ Fallback 1: Gemini Flash ] ──(Success)──→ Complete & Send Output
         │
     (Failed)
         ▼
[ Fallback 2: Groq / Llama ] ──(Success)──→ Complete & Send Output
         │
     (All Failed)
         ▼
Return Graceful Error Message
```

All providers are on free tier — zero cost for development and MVP.

---

## Testing Strategy

All tests are mocked — no real API calls, no billing:

```python
# Example: test analyze_with_fallback without hitting any API
def test_analyze_returns_string(mock_openai_client):
    error_data = {
        "exception": {"type": "QueryException", "value": "timeout"},
        "stacktrace": "#0 app/Models/User.php(42)",
        "request": {"url": "https://app.com/api/users"}
    }
    result = analyze_with_fallback(error_data)
    assert isinstance(result, str)
    assert len(result) > 0
```

```
pytest coverage:
  src/sentry_agent.py      87%
  src/pr_reviewer.py       82%
  src/utils/llm_client.py  91%
```

---

<!-- ## 🗺️ Roadmap

### Phase 1 — MVP (Active) 🚧
- [x] System architecture design
- [x] FastAPI webhook receiver
- [x] LLM fallback chain (DeepSeek → Gemini → Groq)
- [x] Sentry Bug Agent
- [x] GitHub Issue auto-creation
- [x] Testing strategy (pytest + mock LLM)
- [x] Railway deployment workflow
- [ ] PR Auto-Review Agent
- [ ] End-to-end demo

### Phase 2 — Intelligence
- [ ] RAG: index codebase for context-aware review
- [ ] Noise filter: deduplicate Sentry errors
- [ ] Alert Rules Engine: configurable thresholds
- [ ] Multi-project support

### Phase 3 — Scale
- [ ] Monitoring dashboard (real-time job status)
- [ ] Auto-fix commit (with human approval gate)
- [ ] Support multiple VCS (GitLab, Bitbucket)
- [ ] AWS horizontal scaling

--- -->

## Why CodeGuard AI?

Most AI coding tools are **reactive** — they wait for you to ask. CodeGuard AI is **proactive** — it watches your codebase and acts autonomously, like a tireless staff engineer running in the background.

| | Copilot / Cursor | CodeRabbit / SonarQube | **CodeGuard AI** |
|---|---|---|---|
| Mode | Reactive (you ask) | Reactive (waits for PR) | **Proactive (24/7 autonomous)** |
| Triggers | Manual prompt | PR opened | Sentry error, PR, cron, manual |
| Coverage | Write-time only | Review-time only | **Error → Review → Fix → Deploy** |
| AI Reasoning | ✅ | ⚠️ Limited | ✅ Full LLM reasoning |
| Customizable | ❌ Black box | ❌ Black box | ✅ Fully open |
| Cost | Paid | Paid | **Free tier** |
| Language support | Any | Any | **Any** |

> *"Most AI tools wait to be asked. CodeGuard AI watches your codebase and acts — like a tireless staff engineer running in the background."*

CodeGuard AI is not a replacement for those tools — it is proof that any developer can build a system like this from scratch, own it completely, and adapt it to their specific stack and business context.

---

## Author

**Arief Eko** — Backend Engineer · PHP · Node.js · Python · LLM Integration

[![LinkedIn](https://img.shields.io/badge/LinkedIn-ariefeko-blue?style=flat-square)](https://www.linkedin.com/in/arief-eko-wicaksono-4175a12a)
[![GitHub](https://img.shields.io/badge/GitHub-ariefeko-black?style=flat-square&logo=github)](https://github.com/ariefeko)
[![Portfolio](https://img.shields.io/badge/Portfolio-ariefeko.github.io-orange?style=flat-square)](https://ariefeko.github.io)

---

## License

MIT — free to use, fork, and learn from.

---

> *"Tools are temporary. Concepts are permanent."*
> — CodeGuard AI Engineering Guidebook
