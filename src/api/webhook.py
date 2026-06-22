import os
import json
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from src.worker.worker import get_queue, process_github_review, process_sentry_job
from src.agents.sentry_agent import SentryAgent

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
    # PENTING: ambil raw bytes SEBELUM parse JSON. Verifikasi signature
    # butuh body mentah persis seperti yang dikirim Sentry -- kalau kita
    # parse dulu baru re-serialize, byte-nya bisa beda (urutan key, spasi)
    # dan signature akan gagal cocok walau datanya identik.
    raw_body = await request.body()
    signature = request.headers.get("Sentry-Hook-Signature")

    agent = SentryAgent()
    if not agent.verify_signature(raw_body, signature):
        print("[webhook] Sentry signature verification GAGAL — request ditolak")
        return JSONResponse(
            status_code=401,
            content={"status": "rejected", "reason": "invalid signature"},
        )

    payload = json.loads(raw_body)
    resource = request.headers.get("Sentry-Hook-Resource", "unknown")
    action = payload.get("action", "unknown")

    print(f"\n=== SENTRY WEBHOOK: resource={resource} action={action} ===")

    # Resource selain error/issue/event_alert (misal "installation",
    # "comment") tidak relevan untuk bug analysis -- acknowledge saja
    if resource not in ("error", "issue", "event_alert"):
        print(f"[webhook] Resource '{resource}' tidak relevan — diabaikan")
        return {"status": "ignored", "reason": f"resource '{resource}' not handled"}

    error = agent.parse_error(payload)
    if error is None:
        print("[webhook] Tidak ada error context yang bisa diekstrak — diabaikan")
        return {"status": "ignored", "reason": "no parseable error data"}

    # Owner/repo Sentry TIDAK tahu langsung -- ini bukan event GitHub.
    # Perlu konfigurasi mapping project Sentry -> repo GitHub.
    # Sementara hardcode ke env var sampai ada multi-project mapping.
    owner = os.getenv("CODEGUARD_DEFAULT_OWNER")
    repo = os.getenv("CODEGUARD_DEFAULT_REPO")

    if not owner or not repo:
        print("[webhook] CODEGUARD_DEFAULT_OWNER/REPO tidak diset — tidak tahu repo tujuan")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "reason": "no repo mapping configured"},
        )

    print(f"Error: {error['type']} — {error['message']}")
    print(f"File: {error['file']}:{error['line']}")

    # Deduplication via Redis -- satu Sentry issue_id cukup diproses SEKALI
    # dalam 24 jam, terlepas berapa kali Sentry kirim webhook untuk issue yang
    # sama (bisa kirim lewat event_alert DAN issue action=created sekaligus).
    issue_id = error.get("issue_id", "")
    if issue_id:
        import redis as redis_lib
        r = redis_lib.from_url(os.getenv("REDIS_URL"))
        dedup_key = f"codeguard:sentry:processed:{issue_id}"
        if r.exists(dedup_key):
            print(f"[webhook] Issue {issue_id} sudah diproses sebelumnya — diabaikan")
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "already processed"},
            )
        # Set key dengan TTL 24 jam SEBELUM enqueue -- kalau webhook kedua
        # datang milidetik setelah yang pertama, ini pastikan cuma satu job
        # yang masuk queue (bukan dua job yang race condition)
        r.setex(dedup_key, 86400, "1")
    else:
        print("[webhook] Tidak ada issue_id -- deduplication dilewati")

    queue = get_queue()
    job = queue.enqueue(
        process_sentry_job,
        owner, repo,
        error["type"], error["message"], error["file"], error["line"],
        error["related_file_paths"],
        job_timeout=120,
    )

    print(f"[webhook] Sentry job enqueued: {job.id}")

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job.id,
            "message": "Bug analysis queued — GitHub Issue will appear shortly",
        },
    )