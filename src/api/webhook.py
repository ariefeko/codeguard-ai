"""Compose domain-specific webhook routers.

Imports are re-exported temporarily to preserve the public module surface while
callers migrate to ``github_webhook`` and ``sentry_webhook`` directly.
"""

from fastapi import APIRouter

from src.api.github_webhook import (
    GITHUB_PR_FILES_MAX_PAGES,
    GITHUB_PR_FILES_PER_PAGE,
    extract_branch,
    extract_changed_files,
    extract_head_owner,
    extract_repo_info,
    github_webhook,
    router as github_router,
    verify_github_signature,
)
from src.api.sentry_webhook import (
    SENTRY_DEDUP_PENDING_TTL_SECONDS,
    SENTRY_DEDUP_TTL_SECONDS,
    router as sentry_router,
    sentry_webhook,
)
from src.github.repo_policy import is_repo_allowed


router = APIRouter()
router.include_router(github_router)
router.include_router(sentry_router)

__all__ = [
    "GITHUB_PR_FILES_MAX_PAGES",
    "GITHUB_PR_FILES_PER_PAGE",
    "SENTRY_DEDUP_PENDING_TTL_SECONDS",
    "SENTRY_DEDUP_TTL_SECONDS",
    "extract_branch",
    "extract_changed_files",
    "extract_head_owner",
    "extract_repo_info",
    "github_webhook",
    "is_repo_allowed",
    "router",
    "sentry_webhook",
    "verify_github_signature",
]
