import json
from fastapi import APIRouter, Request
from src.context.context_builder import ContextBuilder

router = APIRouter()

@router.post("/webhook/github")
async def github_webhook(request: Request):
    """Handle incoming GitHub webhook events."""
    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "unknown")

    changed_files = extract_changed_files(event_type, payload)

    # Ambil repo info dari payload
    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]
    ref = payload["after"]  # commit SHA terbaru

    # Context Builder jalan otomatis
    cb = ContextBuilder(owner, repo, ref)
    context = cb.build(changed_files)

    print(context)
    return {"status": "received"}


def extract_changed_files(event_type: str, payload: dict) -> list[str]:
    files = set()

    if event_type == "push":
        commits = payload.get("commits", [])
        for commit in commits:
            files.update(commit.get("added", []))
            files.update(commit.get("modified", []))
            # removed files tidak perlu dianalisis

    elif event_type == "pull_request":
        # PR payload tidak langsung kasih list file
        # nanti butuh GitHub API call — placeholder dulu
        pr_number = payload.get("number")
        action = payload.get("action")
        print(f"PR #{pr_number} action: {action}")

    return list(files)


@router.post("/webhook/sentry")
async def sentry_webhook(request: Request):
    """Handle incoming Sentry webhook events."""
    payload = await request.json()
    print("\n=== SENTRY WEBHOOK RECEIVED ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return {"status": "received"}
