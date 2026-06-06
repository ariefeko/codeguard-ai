# 🛡️ CodeGuard AI

> **AI-powered autonomous code guardian.** Detects bugs, reviews PRs, and generates fix suggestions — for any language, any framework.

[![Status](https://img.shields.io/badge/status-active%20development-orange?style=flat-square)](https://github.com/ariefeko/codeguard-ai)
[![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

---

## 🔍 What is CodeGuard AI?

CodeGuard AI is an **autonomous engineering system** that runs in the background — monitoring your codebase, detecting issues, and proposing fixes automatically. Not just a linter, but an agent that reasons.

**4 core capabilities:**

| Capability | Description |
|---|---|
| 🐛 **Bug Detection** | Sentry error received → AI analyzes root cause + stack trace |
| 👁️ **PR Review** | GitHub PR opened → AI reviews diff, flags issues, suggests improvements |
| 🔒 **Security Scan** | Detects SQL injection, exposed secrets, insecure patterns |
| 📈 **Code Quality** | Best practice checks, scalability warnings, refactor suggestions |

**Human-in-the-loop:** AI proposes via PR comment or GitHub Issue — humans approve/merge. No automatic changes without review.

---

## 🏗️ System Architecture

```
Any codebase (any language, any framework)
         ↓ (trigger: error / PR opened / cron)
Sentry Webhook  ──┐
GitHub Actions  ──┤──→ Backend App → Redis Queue (async, 202 instant return)
Scheduled Job   ──┘                        ↓
                              FastAPI Worker (Railway)
                                           ↓
                              Orchestration Layer
                              ├── RAG (optional, Qdrant + nomic-embed-text)
                              └── Prompt Engineering
                                           ↓
                              OpenRouter LLM Gateway
                              ├── DeepSeek V3 (primary)
                              ├── Gemini Flash (fallback #1)
                              └── Groq / Llama (fallback #2)
                                           ↓
                              Output: PR Comment / GitHub Issue / Auto-fix commit
                                           ↓
                              ✅ Human reviews → approve / reject → merge
```

---

## 🚀 Core Features

### 🐛 Sentry Bug Agent
Production error received by Sentry → webhook trigger → AI analyzes within seconds → GitHub Issue created automatically with:
- Root cause analysis
- Files likely involved
- Step-by-step fix suggestion
- Quick fix for immediate production relief
- Prevention strategy going forward

### 👁️ PR Auto-Review Agent
PR opened on GitHub → GitHub Actions trigger → AI reviews diff → comments directly on PR with:
- Bug detection per line
- Security vulnerability check
- Best practice violations
- Performance concerns
- Improvement suggestions

### 🔒 Security Scanner
- SQL Injection pattern detection
- Exposed API keys / secrets
- Insecure direct object reference
- Missing authentication checks
- Dependency vulnerability hints

### 📈 Code Quality Analyzer
- SOLID principle violations
- N+1 query detection
- Scalability bottlenecks
- Dead code identification
- Refactor opportunities

---

## 🛠️ Tech Stack

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
│   ├── sentry_agent.py      # Sentry webhook receiver + bug analysis
│   ├── pr_reviewer.py       # PR diff analyzer + GitHub commenter
│   ├── security_scanner.py  # Security pattern detection
│   ├── orchestrator.py      # Main LangChain orchestration
│   ├── rag/
│   │   ├── indexer.py       # Codebase indexing for RAG
│   │   └── retriever.py     # Context retrieval
│   └── utils/
│       ├── llm_client.py    # OpenRouter + fallback chain
│       └── github_client.py # GitHub API wrapper
├── tests/
│   ├── conftest.py          # Mock LLM fixtures
│   ├── test_sentry_agent.py
│   └── test_pr_reviewer.py
├── docs/
│   ├── architecture.md
│   └── deployment.md
├── .env.example
├── requirements.txt
├── Procfile                 # Railway deploy config
└── README.md
```

---

## ⚡ Quick Start

### Prerequisites
- Python 3.11+
- Redis (local or Railway)
- OpenRouter API key (free tier)
- GitHub Personal Access Token
- Sentry account (free tier)

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
DEEPSEEK_API_KEY=sk-xxxx        # platform.deepseek.com (free tier)
OPENROUTER_API_KEY=sk-or-xxxx   # openrouter.ai (free tier)
GROQ_API_KEY=gsk_xxxx           # console.groq.com (free tier)

# GitHub
GITHUB_TOKEN=ghp_xxxx
GITHUB_REPO=username/your-repo

# Sentry
SENTRY_WEBHOOK_SECRET=xxxx

# App
APP_ENV=local                   # local | staging | production
LOG_LEVEL=DEBUG
```

### Run Locally

```bash
# Start FastAPI server
uvicorn src.sentry_agent:app --reload --port 8000

# Test health check
curl http://localhost:8000/
# → {"status": "ok", "deepseek_key_set": true, ...}

# Send a test Sentry payload
curl -X POST http://localhost:8000/webhook/sentry \
  -H "Content-Type: application/json" \
  -d '{
    "exception": {"type": "QueryException", "value": "SQLSTATE timeout"},
    "stacktrace": "#0 app/Models/User.php(42)",
    "request": {"url": "https://yourapp.com/api/users"}
  }'
```

### Run Tests

```bash
# Run all tests (no real API calls — LLM is mocked)
pytest -v

# Run with coverage report
pytest --cov=src --cov-report=term-missing
```

---

## 🚂 Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and initialize
railway login
railway init

# Set environment variables
railway variables set DEEPSEEK_API_KEY=sk-xxxx
railway variables set GITHUB_TOKEN=ghp_xxxx
railway variables set SENTRY_WEBHOOK_SECRET=xxxx
railway variables set APP_ENV=production

# Deploy
railway up

# Get your public URL
railway domain
# → https://codeguard-ai-production.up.railway.app
```

---

## 🔄 LLM Fallback Chain

CodeGuard AI does not rely on a single provider. If one is down or rate-limited, the system automatically falls back:

```
Incoming request
     ↓
DeepSeek V3 ──→ OK? → Use it
     ↓ (failed / rate limited)
Gemini Flash ──→ OK? → Use it
     ↓ (failed)
Groq / Llama ──→ OK? → Use it
     ↓ (all failed)
Return graceful error message
```

All providers are on free tier — zero cost for development and MVP.

---

## 🧪 Testing Strategy

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

## 💡 Why CodeGuard AI?

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

## 👨‍💻 Author

**Arief Eko** — Senior Backend Engineer (Fullstack Capable)

- 9+ years production experience: Laravel, Node.js, Python
- Clients: BRI (digital banking), Nestlé (enterprise)
- Building AI-powered systems for real-world engineering problems

[![GitHub](https://img.shields.io/badge/GitHub-ariefeko-black?style=flat-square&logo=github)](https://github.com/ariefeko)
[![Portfolio](https://img.shields.io/badge/Portfolio-ariefeko.github.io-blue?style=flat-square)](https://ariefeko.github.io)

---

## 📄 License

MIT — free to use, fork, and learn from.

---

> *"Tools are temporary. Concepts are permanent."*
> — CodeGuard AI Engineering Guidebook
