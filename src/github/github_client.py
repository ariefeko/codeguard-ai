import logging
import math
import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from src.config import (
    CODEGUARD_APP_ID,
    DEFAULT_REPOSITORY_BRANCH,
    GITHUB_RETRY_BASE_DELAY_SECONDS,
    GITHUB_RETRY_MAX_ATTEMPTS,
    GITHUB_RETRY_MAX_DELAY_SECONDS,
    GITHUB_STATUS_DESCRIPTION_MAX_LENGTH,
    HTTP_REQUEST_TIMEOUT_SECONDS,
)
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
        sleep: Callable[[float], None] | None = None,
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
        self._sleep = sleep or time.sleep
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

    @staticmethod
    def _is_transient_response(response: httpx.Response) -> bool:
        return (
            response.status_code == 429
            or response.status_code >= 500
            or (
                response.status_code == 403
                and response.headers.get("X-RateLimit-Remaining") == "0"
            )
        )

    @staticmethod
    def _is_transient_exception(exc: httpx.RequestError) -> bool:
        return isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.ProxyError,
                httpx.RemoteProtocolError,
            ),
        )

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        retry_after = (
            response.headers.get("Retry-After")
            if response is not None
            else None
        )
        if retry_after is not None:
            try:
                retry_after_seconds = float(retry_after)
                if math.isfinite(retry_after_seconds) and retry_after_seconds >= 0:
                    return min(
                        retry_after_seconds,
                        GITHUB_RETRY_MAX_DELAY_SECONDS,
                    )
            except (TypeError, ValueError):
                pass

        return min(
            GITHUB_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
            GITHUB_RETRY_MAX_DELAY_SECONDS,
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> httpx.Response:
        request = getattr(self.http_client, method)
        for attempt in range(1, GITHUB_RETRY_MAX_ATTEMPTS + 1):
            response = None
            try:
                response = request(url, **kwargs)
                if not self._is_transient_response(response):
                    return response
            except httpx.RequestError as exc:
                if not self._is_transient_exception(exc):
                    raise
                if attempt == GITHUB_RETRY_MAX_ATTEMPTS:
                    raise

            if attempt == GITHUB_RETRY_MAX_ATTEMPTS:
                if response is None:
                    raise RuntimeError("GitHub request completed without a response")
                return response

            delay = self._retry_delay(response, attempt)
            logger.warning(
                "Retrying transient GitHub API failure",
                extra={
                    "github_operation": operation,
                    "retry_attempt": attempt + 1,
                    "retry_delay_seconds": delay,
                    "status_code": (
                        response.status_code if response is not None else None
                    ),
                },
            )
            self._sleep(delay)

        raise RuntimeError("GitHub retry loop ended unexpectedly")

    @staticmethod
    def _parse_json_response(
        response: httpx.Response,
        expected_type: type[Any],
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

    @staticmethod
    def _log_request_exception(exc: httpx.HTTPError, operation: str) -> None:
        if isinstance(exc, httpx.TimeoutException):
            category = "timeout"
            status_code = None
        elif isinstance(exc, httpx.HTTPStatusError):
            category = "http_status"
            status_code = exc.response.status_code
        else:
            category = "transport_error"
            status_code = None

        logger.warning(
            "GitHub API request raised an exception",
            extra={
                "github_operation": operation,
                "github_error_category": category,
                "exception_class": type(exc).__name__,
                "status_code": status_code,
            },
            exc_info=True,
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
            "description": description[:GITHUB_STATUS_DESCRIPTION_MAX_LENGTH],
            "context": context,
        }
        if target_url:
            payload["target_url"] = target_url

        try:
            response = self._request_with_retry(
                "post",
                url,
                operation="setting commit status",
                headers=self.headers,
                json=payload,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 201:
                logger.info(
                    "GitHub commit status set",
                    extra={"status_context": context, "status_state": state},
                )
                return True

            self._log_http_failure(response, "setting commit status")
            return False
        except httpx.TimeoutException as exc:
            self._log_request_exception(exc, "setting commit status")
            return False
        except httpx.HTTPStatusError as exc:
            self._log_request_exception(exc, "setting commit status")
            return False
        except httpx.RequestError as exc:
            self._log_request_exception(exc, "setting commit status")
            return False

    def get_default_branch(self) -> str:
        """Get the target branch for Sentry context, preferring the environment override."""
        env_branch = os.getenv("CODEGUARD_DEFAULT_BRANCH")
        if env_branch:
            logger.info("Using configured GitHub default branch")
            return env_branch

        try:
            response = self._request_with_retry(
                "get",
                self.base_url,
                operation="getting the default branch",
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
                    logger.info("GitHub default branch resolved")
                    return default_branch

            self._log_http_failure(response, "getting the default branch")
        except httpx.TimeoutException as exc:
            self._log_request_exception(exc, "getting the default branch")
        except httpx.HTTPStatusError as exc:
            self._log_request_exception(exc, "getting the default branch")
        except httpx.RequestError as exc:
            self._log_request_exception(exc, "getting the default branch")

        return os.getenv("CODEGUARD_DEFAULT_BRANCH", DEFAULT_REPOSITORY_BRANCH)

    def get_open_pr_for_branch(self, branch: str, head_owner: str | None = None) -> int | None:
        """
        Find an open pull request for a branch.
        Return its number when found, otherwise None.
        """
        owner = head_owner or self.owner
        url = f"{self.base_url}/pulls"
        try:
            response = self._request_with_retry(
                "get",
                url,
                operation="getting open pull requests",
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
                        logger.warning("Invalid GitHub pull request response shape")
                        return None
                    pr_number = first_pr["number"]
                    logger.info("Open GitHub pull request found")
                    return pr_number
                else:
                    logger.info("No open GitHub pull request found")
                    return None
            else:
                self._log_http_failure(response, "getting open pull requests")
                return None
        except httpx.TimeoutException as exc:
            self._log_request_exception(exc, "getting open pull requests")
            return None
        except httpx.HTTPStatusError as exc:
            self._log_request_exception(exc, "getting open pull requests")
            return None
        except httpx.RequestError as exc:
            self._log_request_exception(exc, "getting open pull requests")
            return None

    def post_pr_comment(self, pr_number: int, body: str) -> bool:
        """
        Post a pull request comment.
        Return True on success.
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
            response = self._request_with_retry(
                "post",
                url,
                operation="posting a pull request comment",
                headers=self.headers,
                json=payload,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 201:
                logger.info("GitHub pull request comment posted")
                return True
            else:
                self._log_http_failure(response, "posting a pull request comment")
                return False
        except httpx.TimeoutException as exc:
            self._log_request_exception(exc, "posting a pull request comment")
            return False
        except httpx.HTTPStatusError as exc:
            self._log_request_exception(exc, "posting a pull request comment")
            return False
        except httpx.RequestError as exc:
            self._log_request_exception(exc, "posting a pull request comment")
            return False

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> bool:
        """
        Create a GitHub issue as a fallback when there is no pull request or
        as the entry point for Sentry bug analysis.
        Labels default to [CODEGUARD_APP_ID] when omitted, preserving the
        existing process_github_review behavior.
        """
        url = f"{self.base_url}/issues"
        payload = {
            "title": title,
            "body": body,
            "labels": labels if labels is not None else [CODEGUARD_APP_ID],
        }
        try:
            response = self._request_with_retry(
                "post",
                url,
                operation="creating an issue",
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
                    logger.warning("Invalid GitHub issue response shape")
                    return False
                logger.info("GitHub issue created")
                return True
            else:
                self._log_http_failure(response, "creating an issue")
                return False
        except httpx.TimeoutException as exc:
            self._log_request_exception(exc, "creating an issue")
            return False
        except httpx.HTTPStatusError as exc:
            self._log_request_exception(exc, "creating an issue")
            return False
        except httpx.RequestError as exc:
            self._log_request_exception(exc, "creating an issue")
            return False
