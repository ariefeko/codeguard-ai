import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from src.agents.sentry_agent import SentryAgent
from src.worker.redis_connection import get_queue, get_redis_connection
from src.worker.worker import process_sentry_job


load_dotenv()

router = APIRouter()

SENTRY_DEDUP_TTL_SECONDS = 86400
SENTRY_DEDUP_PENDING_TTL_SECONDS = int(
    os.getenv("SENTRY_DEDUP_PENDING_TTL_SECONDS", "60")
)


@router.post("/webhook/sentry")
async def sentry_webhook(request: Request):
    # Signature verification requires the exact bytes sent by Sentry.
    raw_body = await request.body()
    signature = request.headers.get("Sentry-Hook-Signature")

    agent = SentryAgent()
    if not agent.verify_signature(raw_body, signature):
        print("[webhook] Sentry signature verification failed — request rejected")
        return JSONResponse(
            status_code=401,
            content={"status": "rejected"},
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        print("[webhook] Sentry payload contains invalid JSON")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    resource = request.headers.get("Sentry-Hook-Resource", "unknown")
    action = payload.get("action", "unknown")
    print(f"\n=== SENTRY WEBHOOK: resource={resource} action={action} ===")

    if resource not in ("error", "issue", "event_alert"):
        print(f"[webhook] Resource '{resource}' is not relevant — skipping")
        return {"status": "skipped", "reason": f"resource '{resource}' not handled"}

    error = agent.parse_error(payload)
    if error is None:
        print("[webhook] No error context could be extracted — skipping")
        return {"status": "skipped", "reason": "no parseable error data"}

    required_error_fields = (
        "type",
        "message",
        "file",
        "line",
        "related_file_paths",
    )
    if not isinstance(error, dict) or not all(
        field in error for field in required_error_fields
    ):
        print("[webhook] Parsed error data is invalid — request rejected")
        return JSONResponse(
            status_code=400,
            content={"status": "rejected", "reason": "invalid error data"},
        )

    owner = os.getenv("CODEGUARD_DEFAULT_OWNER")
    repo = os.getenv("CODEGUARD_DEFAULT_REPO")
    if not owner or not repo:
        print("[webhook] CODEGUARD_DEFAULT_OWNER/REPO is not configured — target unknown")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "reason": "no repo mapping configured"},
        )

    print(f"Error: {error['type']} — {error['message']}")
    print(f"File: {error['file']}:{error['line']}")

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
            print(f"[webhook] Issue {issue_id} was already processed — ignoring")
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "already processed"},
            )
    else:
        print("[webhook] No issue_id was provided — skipping deduplication")

    try:
        queue = get_queue()
        job = queue.enqueue(
            process_sentry_job,
            owner,
            repo,
            error["type"],
            error["message"],
            error["file"],
            error["line"],
            error["related_file_paths"],
            job_timeout=120,
        )
    except Exception:
        if dedup_key and dedup_redis:
            dedup_redis.delete(dedup_key)
        raise

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
