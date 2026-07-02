import os
import json
import hashlib
import hmac
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from src.worker.worker import (
    get_queue,
    get_redis_connection,
    process_github_review,
    process_sentry_job,
)
from src.agents.sentry_agent import SentryAgent
from src.github.repo_policy import is_repo_allowed

load_dotenv()

router = APIRouter()

SENTRY_DEDUP_TTL_SECONDS = 86400
# Retry window if enqueue fails after the pending dedup key is acquired.
SENTRY_DEDUP_PENDING_TTL_SECONDS = int(
    os.getenv("SENTRY_DEDUP_PENDING_TTL_SECONDS", "60")
)


@router.post("/webhook/github")
async def github_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(raw_body, signature):
        print("[webhook] GitHub signature verification GAGAL — request ditolak")
        return JSONResponse(
            status_code=401,
            content={"status": "rejected"},
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        print("[webhook] GitHub payload JSON tidak valid")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    event_type = request.headers.get("X-GitHub-Event", "unknown")

    print(f"\n=== GITHUB WEBHOOK: {event_type} ===")

    repo_info = extract_repo_info(payload)
    if repo_info is None:
        print("[webhook] GitHub payload tidak memiliki repository owner/name")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    owner, repo = repo_info
    if not is_repo_allowed(owner, repo):
        print(f"[webhook] GitHub repo tidak diizinkan: {owner}/{repo}")
        return JSONResponse(
            status_code=403,
            content={"status": "rejected"},
        )

    changed_files = extract_changed_files(event_type, payload)

    print(f"Changed files ({len(changed_files)}):")
    for f in changed_files:
        print(f"  - {f}")

    if not changed_files:
        return {"status": "skipped", "reason": "no changed files"}

    ref = payload.get("after") or payload.get("pull_request", {}).get("head", {}).get("sha")
    branch = extract_branch(event_type, payload)
    pr_number = payload.get("number") if event_type == "pull_request" else None
    head_owner = extract_head_owner(event_type, payload)

    # Push job ke Redis Queue → instant response
    queue = get_queue()
    job = queue.enqueue(
        process_github_review,  # ← langsung function, bukan string
        owner, repo, ref, branch, changed_files, pr_number, head_owner,
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


def extract_repo_info(payload: dict) -> tuple[str, str] | None:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return None

    owner_data = repository.get("owner")
    if not isinstance(owner_data, dict):
        return None

    owner = owner_data.get("login")
    repo = repository.get("name")
    if not owner or not repo:
        return None

    return owner, repo


def verify_github_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Verifikasi signature GitHub webhook.
    GitHub mengirim HMAC SHA-256 sebagai "sha256=<hex digest>".
    """
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        print("[webhook] GITHUB_WEBHOOK_SECRET tidak diset -- menolak request")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        print("[webhook] Header X-Hub-Signature-256 tidak valid")
        return False

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


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
        page = 1

        while True:
            response = httpx.get(
                url,
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=10,
            )

            if response.status_code != 200:
                print(f"[webhook] Failed to fetch PR files: HTTP {response.status_code}")
                break

            page_files = response.json()
            for f in page_files:
                if f.get("status") != "removed":
                    files.add(f["filename"])

            if len(page_files) < 100:
                break
            page += 1

    return list(files)


def extract_branch(event_type: str, payload: dict) -> str | None:
    if event_type == "push":
        ref = payload.get("ref", "")
        return ref.replace("refs/heads/", "") if ref else None
    if event_type == "pull_request":
        return payload.get("pull_request", {}).get("head", {}).get("ref")
    return None


def extract_head_owner(event_type: str, payload: dict) -> str | None:
    if event_type == "pull_request":
        return (
            payload.get("pull_request", {})
            .get("head", {})
            .get("repo", {})
            .get("owner", {})
            .get("login")
        )
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
            content={"status": "rejected"},
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        print("[webhook] Sentry payload JSON tidak valid")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    resource = request.headers.get("Sentry-Hook-Resource", "unknown")
    action = payload.get("action", "unknown")

    print(f"\n=== SENTRY WEBHOOK: resource={resource} action={action} ===")

    # Resource selain error/issue/event_alert (misal "installation",
    # "comment") tidak relevan untuk bug analysis -- acknowledge saja
    if resource not in ("error", "issue", "event_alert"):
        print(f"[webhook] Resource '{resource}' tidak relevan — dilewati")
        return {"status": "skipped", "reason": f"resource '{resource}' not handled"}

    error = agent.parse_error(payload)
    if error is None:
        print("[webhook] Tidak ada error context yang bisa diekstrak — dilewati")
        return {"status": "skipped", "reason": "no parseable error data"}

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

    # Deduplication via Redis -- gunakan pending lock pendek sebelum enqueue,
    # lalu promote ke queued lock 24 jam hanya setelah enqueue sukses. Saat
    # enqueue gagal, jangan delete key: delete bisa balapan dengan request lain
    # yang sudah melihat lock dan mengabaikan event. Pending TTL membuat retry
    # berikutnya bisa masuk setelah window pendek berakhir.
    issue_id = error.get("issue_id", "")
    dedup_key = None
    dedup_redis = None
    if issue_id:
        dedup_redis = get_redis_connection()
        dedup_key = f"codeguard:sentry:processed:{issue_id}"
        lock_acquired = dedup_redis.set(
            dedup_key,
            "pending",
            ex=SENTRY_DEDUP_PENDING_TTL_SECONDS,
            nx=True,
        )
        if not lock_acquired:
            print(f"[webhook] Issue {issue_id} sudah diproses sebelumnya — diabaikan")
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "already processed"},
            )
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

    if dedup_key and dedup_redis:
        dedup_redis.set(dedup_key, f"queued:{job.id}", ex=SENTRY_DEDUP_TTL_SECONDS)

    print(f"[webhook] Sentry job enqueued: {job.id}")

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job.id,
            "message": "Bug analysis queued — GitHub Issue will appear shortly",
        },
    )
