import os
import re


OWNER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_valid_repo_name(owner: str, repo: str) -> bool:
    return bool(OWNER_NAME_RE.fullmatch(owner) and REPO_NAME_RE.fullmatch(repo))


def normalize_repo(owner: str, repo: str) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}"


def get_allowed_repos() -> set[str]:
    """
    Return the repo allowlist for GitHub operations.

    CODEGUARD_ALLOWED_REPOS accepts comma-separated owner/repo entries.
    CODEGUARD_DEFAULT_OWNER/REPO is treated as the single-repo fallback for
    deployments that only serve one target repository.
    """
    configured = os.getenv("CODEGUARD_ALLOWED_REPOS", "")
    repos = set()
    for item in configured.split(","):
        if not item.strip() or "/" not in item:
            continue

        owner, repo = item.strip().split("/", 1)
        if is_valid_repo_name(owner, repo):
            repos.add(normalize_repo(owner, repo))

    default_owner = os.getenv("CODEGUARD_DEFAULT_OWNER")
    default_repo = os.getenv("CODEGUARD_DEFAULT_REPO")
    if (
        default_owner
        and default_repo
        and is_valid_repo_name(default_owner, default_repo)
    ):
        repos.add(normalize_repo(default_owner, default_repo))

    return repos


def is_repo_allowed(owner: str, repo: str) -> bool:
    if not is_valid_repo_name(owner, repo):
        return False

    allowed_repos = get_allowed_repos()
    if not allowed_repos:
        return False

    return normalize_repo(owner, repo) in allowed_repos
