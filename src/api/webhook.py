import json
from fastapi import APIRouter, Request
from dotenv import load_dotenv
from src.context.context_builder import ContextBuilder
from src.orchestration.orchestrator import Orchestrator
load_dotenv()

router = APIRouter()

@router.post("/webhook/github")
async def github_webhook(request: Request):
    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "unknown")

    print(f"\n=== GITHUB WEBHOOK: {event_type} ===")

    changed_files = extract_changed_files(event_type, payload)

    print(f"Changed files ({len(changed_files)}):")
    for f in changed_files:
        print(f"  - {f}")

    if not changed_files:
        return {"status": "skipped", "reason": "no changed files"}

    # Ambil repo info dari payload
    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]
    ref = payload["after"]

    # Context Builder
    cb = ContextBuilder(owner, repo, ref)
    context = cb.build(changed_files)

    if not context["changed_files"]:
        return {"status": "skipped", "reason": "no analyzable files"}

    # Orchestration → LLM
    orchestrator = Orchestrator()
    result = orchestrator.review_code(context)

    print("\n=== LLM REVIEW RESULT ===")
    print(result)

    return {"status": "reviewed", "result": result}


def extract_changed_files(event_type: str, payload: dict) -> list[str]:
    files = set()

    if event_type == "push":
        commits = payload.get("commits", [])
        for commit in commits:
            files.update(commit.get("added", []))
            files.update(commit.get("modified", []))

    elif event_type == "pull_request":
        pr_number = payload.get("number")
        action = payload.get("action")
        print(f"PR #{pr_number} action: {action}")

    return list(files)


@router.post("/webhook/sentry")
async def sentry_webhook(request: Request):
    payload = await request.json()
    print("\n=== SENTRY WEBHOOK RECEIVED ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return {"status": "received"}