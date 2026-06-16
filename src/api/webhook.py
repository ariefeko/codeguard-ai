import os
import json
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from src.worker.worker import get_queue
from src.worker.worker import get_queue, process_github_review

load_dotenv()

router = APIRouter()


@router.post("/webhook/github")
async def github_webhook(request: Request):
    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "unknown")

    print(f"\n=== GITHUB WEBHOOK: {event_type} ===")

    # Ambil repo info dari payload
    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]

    changed_files = extract_changed_files(event_type, payload)

    print(f"Changed files ({len(changed_files)}):")
    for f in changed_files:
        print(f"  - {f}")

    if not changed_files:
        return {"status": "skipped", "reason": "no changed files"}

    ref = payload.get("after") or payload.get("pull_request", {}).get("head", {}).get("sha")
    branch = extract_branch(event_type, payload)

    # Push job ke Redis Queue → instant response
    queue = get_queue()
    job = queue.enqueue(
        process_github_review,  # ← langsung function, bukan string
        owner, repo, ref, branch, changed_files,
        job_timeout=120,
    )

    print(f"[webhook] Job enqueued: {job.id}")

    return JSONResponse(
        status_code = 202,
        content = {
            "status": "accepted",
            "job_id": job.id,
            "message": "Review queued — PR comment will appear shortly"
        }
    )


def extract_changed_files(event_type: str, payload: dict) -> list[str]:
    files = set()

    if event_type == "push":
        commits = payload.get("commits", [])
        for commit in commits:
            files.update(commit.get("added", []))
            files.update(commit.get("modified", []))

    elif event_type == "pull_request":
        action = payload.get("action")
        pr_number = payload.get("number")
        print(f"PR #{pr_number} action: {action}")

        # Hanya proses saat PR dibuka atau di-sync
        if action not in ("opened", "synchronize"):
            return []

        # Fetch changed files via GitHub API
        owner = payload["repository"]["owner"]["login"]
        repo = payload["repository"]["name"]
        token = os.getenv("GITHUB_PAT_TOKEN")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        response = httpx.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            for f in response.json():
                if f.get("status") != "removed":
                    files.add(f["filename"])

    return list(files)


def extract_branch(event_type: str, payload: dict) -> str | None:
    if event_type == "push":
        ref = payload.get("ref", "")
        return ref.replace("refs/heads/", "") if ref else None
    if event_type == "pull_request":
        return payload.get("pull_request", {}).get("head", {}).get("ref")
    return None


@router.post("/webhook/sentry")
async def sentry_webhook(request: Request):
    payload = await request.json()
    print("\n=== SENTRY WEBHOOK RECEIVED ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return {"status": "received"}
