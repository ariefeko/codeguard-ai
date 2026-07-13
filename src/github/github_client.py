import logging
import os
from typing import Any

import httpx
from src.config import CODEGUARD_APP_ID, HTTP_REQUEST_TIMEOUT_SECONDS
from src.github.http_client import build_github_headers, get_github_http_client
from src.github.repo_policy import is_repo_allowed, is_valid_repo_name


MAX_GITHUB_PR_NUMBER = 2_147_483_647
logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(
        self,
        owner: str,
        repo: str,
        http_client: httpx.Client | None = None,
    ):
        if not is_valid_repo_name(owner, repo):
            raise ValueError("Invalid owner or repo name format")

        if not is_repo_allowed(owner, repo):
            raise PermissionError(f"Repository is not allowed: {owner}/{repo}")

        self.owner = owner
        self.repo = repo
        self.token = os.getenv("GITHUB_PAT_TOKEN")
        self.headers = build_github_headers(self.token)
        self.http_client = http_client or get_github_http_client()
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

    @staticmethod
    def _parse_json_response(
        response: httpx.Response,
        expected_type: type,
        operation: str,
    ) -> Any | None:
        """Parse a GitHub response without allowing malformed JSON to escape."""
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            logger.warning(
                "GitHub API returned invalid JSON",
                extra={"github_operation": operation},
            )
            return None

        if not isinstance(payload, expected_type):
            logger.warning(
                "GitHub API returned an unexpected JSON type",
                extra={
                    "github_operation": operation,
                    "expected_type": expected_type.__name__,
                },
            )
            return None

        return payload

    @staticmethod
    def _log_http_failure(response: httpx.Response, operation: str) -> None:
        status_code = response.status_code
        rate_limit_remaining = response.headers.get("X-RateLimit-Remaining")

        if status_code == 429 or rate_limit_remaining == "0":
            category = "rate_limit"
        elif status_code in {401, 403}:
            category = "authentication"
        elif status_code >= 500:
            category = "server_error"
        else:
            category = "api_error"

        logger.warning(
            "GitHub API request failed",
            extra={
                "github_operation": operation,
                "status_code": status_code,
                "github_error_category": category,
            },
        )

    def set_commit_status(
        self,
        sha: str,
        state: str,
        description: str,
        context: str = CODEGUARD_APP_ID,
        target_url: str | None = None,
    ) -> bool:
        """
        Set GitHub commit status so branch protection can block PR merges.
        state must be one of: error, failure, pending, success.
        """
        if state not in {"error", "failure", "pending", "success"}:
            raise ValueError("Invalid commit status state")

        url = f"{self.base_url}/statuses/{sha}"
        payload = {
            "state": state,
            "description": description[:140],
            "context": context,
        }
        if target_url:
            payload["target_url"] = target_url

        try:
            response = self.http_client.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 201:
                print(f"[GitHubClient] Commit status set: {context}={state}")
                return True

            self._log_http_failure(response, "setting commit status")
            return False
        except httpx.HTTPError:
            logger.exception(
                "GitHub HTTP error while setting commit status",
                extra={"github_operation": "setting commit status"},
            )
            return False

    def get_default_branch(self) -> str:
        """Ambil branch target untuk Sentry context, env override lebih dulu."""
        env_branch = os.getenv("CODEGUARD_DEFAULT_BRANCH")
        if env_branch:
            print(f"[GitHubClient] Default branch override: {env_branch}")
            return env_branch

        try:
            response = self.http_client.get(
                self.base_url,
                headers=self.headers,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                payload = self._parse_json_response(
                    response,
                    dict,
                    "getting the default branch",
                )
                default_branch = payload.get("default_branch") if payload else None
                if isinstance(default_branch, str) and default_branch:
                    print(f"[GitHubClient] Default branch: {default_branch}")
                    return default_branch

            self._log_http_failure(response, "getting the default branch")
        except httpx.HTTPError:
            logger.exception(
                "GitHub HTTP error while getting the default branch",
                extra={"github_operation": "getting the default branch"},
            )

        return os.getenv("CODEGUARD_DEFAULT_BRANCH", "main")

    def get_open_pr_for_branch(self, branch: str, head_owner: str | None = None) -> int | None:
        """
        Cari PR yang open untuk branch tertentu.
        Return PR number kalau ada, None kalau tidak ada.
        """
        owner = head_owner or self.owner
        url = f"{self.base_url}/pulls"
        try:
            response = self.http_client.get(
                url,
                headers=self.headers,
                params={"state": "open", "head": f"{owner}:{branch}"},
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                prs = self._parse_json_response(
                    response,
                    list,
                    "getting open pull requests",
                )
                if prs is None:
                    return None
                if prs:
                    first_pr = prs[0]
                    if not isinstance(first_pr, dict) or not isinstance(
                        first_pr.get("number"), int
                    ):
                        print("[GitHubClient] Invalid pull request response shape")
                        return None
                    pr_number = first_pr["number"]
                    print(f"[GitHubClient] Found open PR #{pr_number} for branch: {owner}:{branch}")
                    return pr_number
                else:
                    print(f"[GitHubClient] No open PR for branch: {owner}:{branch}")
                    return None
            else:
                self._log_http_failure(response, "getting open pull requests")
                return None
        except httpx.HTTPError:
            logger.exception(
                "GitHub HTTP error while getting open pull requests",
                extra={"github_operation": "getting open pull requests"},
            )
            return None

    def post_pr_comment(self, pr_number: int, body: str) -> bool:
        """
        Post comment ke PR.
        Return True kalau berhasil.
        """
        if (
            not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number <= 0
            or pr_number > MAX_GITHUB_PR_NUMBER
        ):
            raise ValueError("pr_number must be a positive integer within range")

        url = f"{self.base_url}/issues/{pr_number}/comments"
        payload = {"body": body}
        try:
            response = self.http_client.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 201:
                print(f"[GitHubClient] Comment posted to PR #{pr_number} ✅")
                return True
            else:
                self._log_http_failure(response, "posting a pull request comment")
                return False
        except httpx.HTTPError:
            logger.exception(
                "GitHub HTTP error while posting a pull request comment",
                extra={"github_operation": "posting a pull request comment"},
            )
            return False

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> bool:
        """
        Buat GitHub Issue — fallback kalau tidak ada PR, atau entry point
        untuk Sentry bug analysis.
        labels default [CODEGUARD_APP_ID] kalau tidak di-pass -- perilaku
        lama (dipanggil dari process_github_review) tidak berubah.
        """
        url = f"{self.base_url}/issues"
        payload = {
            "title": title,
            "body": body,
            "labels": labels if labels is not None else [CODEGUARD_APP_ID],
        }
        try:
            response = self.http_client.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 201:
                response_payload = self._parse_json_response(
                    response,
                    dict,
                    "creating an issue",
                )
                if response_payload is None:
                    return False
                issue_url = response_payload.get("html_url")
                if not isinstance(issue_url, str) or not issue_url:
                    print("[GitHubClient] Invalid issue response shape")
                    return False
                print(f"[GitHubClient] Issue created: {issue_url} ✅")
                return True
            else:
                self._log_http_failure(response, "creating an issue")
                return False
        except httpx.HTTPError:
            logger.exception(
                "GitHub HTTP error while creating an issue",
                extra={"github_operation": "creating an issue"},
            )
            return False
