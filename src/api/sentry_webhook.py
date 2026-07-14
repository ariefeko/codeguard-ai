import json
import logging
import os

from fastapi import APIRouter, Request
from dotenv import load_dotenv

from src.agents.sentry_agent import SentryAgent
from src.api.responses import webhook_response
from src.config import RQ_JOB_TIMEOUT_SECONDS
from src.worker.redis_connection import get_queue, get_redis_connection
from src.worker.worker import process_sentry_job


load_dotenv()

router = APIRouter()
logger = logging.getLogger(__name__)

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
        logger.warning("Sentry signature verification failed")
        return webhook_response(401, "rejected", "invalid signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Sentry payload contains invalid JSON")
        return webhook_response(400, "rejected", "invalid JSON payload")

    resource = request.headers.get("Sentry-Hook-Resource", "unknown")
    action = payload.get("action", "unknown")
    logger.info(
        "Sentry webhook received",
        extra={"sentry_resource": resource, "sentry_action": action},
    )

    if resource not in ("error", "issue", "event_alert"):
        logger.info("Sentry resource skipped", extra={"sentry_resource": resource})
        return webhook_response(
            200,
            "skipped",
            f"resource '{resource}' not handled",
        )

    error = agent.parse_error(payload)
    if error is None:
        logger.info("Sentry payload has no parseable error data")
        return webhook_response(200, "skipped", "no parseable error data")

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
        logger.warning("Sentry parsed error data is invalid")
        return webhook_response(400, "rejected", "invalid error data")

    owner = os.getenv("CODEGUARD_DEFAULT_OWNER")
    repo = os.getenv("CODEGUARD_DEFAULT_REPO")
    if not owner or not repo:
        logger.error("Sentry repository mapping is not configured")
        return webhook_response(500, "error", "no repo mapping configured")

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
            logger.info("Duplicate Sentry issue ignored")
            return webhook_response(200, "ignored", "already processed")
    else:
        logger.info("Sentry issue has no deduplication identifier")

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
            job_timeout=RQ_JOB_TIMEOUT_SECONDS,
        )
    except Exception:
        if dedup_key and dedup_redis:
            dedup_redis.delete(dedup_key)
        raise

    if dedup_key and dedup_redis:
        dedup_redis.set(dedup_key, f"queued:{job.id}", ex=SENTRY_DEDUP_TTL_SECONDS)

    logger.info("Sentry job enqueued", extra={"job_id": job.id})
    return webhook_response(
        202,
        "accepted",
        "bug analysis queued",
        job_id=job.id,
    )
