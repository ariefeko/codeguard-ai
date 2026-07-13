import os
from typing import Any

import httpx
from src.github.repo_policy import is_repo_allowed, is_valid_repo_name


class GitHubClient:
    def __init__(self, owner: str, repo: str):
        if not is_valid_repo_name(owner, repo):
            raise ValueError("Invalid owner or repo name format")

        if not is_repo_allowed(owner, repo):
            raise PermissionError(f"Repository is not allowed: {owner}/{repo}")

        self.owner = owner
        self.repo = repo
        self.token = os.getenv("GITHUB_PAT_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
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
            print(f"[GitHubClient] Invalid JSON response while {operation}")
            return None

        if not isinstance(payload, expected_type):
            print(
                f"[GitHubClient] Unexpected JSON response type while {operation}: "
                f"expected {expected_type.__name__}"
            )
            return None

        return payload

    def set_commit_status(
        self,
        sha: str,
        state: str,
        description: str,
        context: str = "codeguard-ai",
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
            response = httpx.post(url, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 201:
                print(f"[GitHubClient] Commit status set: {context}={state}")
                return True

            print(f"[GitHubClient] Failed to set commit status: HTTP {response.status_code}")
            return False
        except Exception as e:
            print(f"[GitHubClient] Error setting commit status: {e}")
            return False

    def get_default_branch(self) -> str:
        """Ambil branch target untuk Sentry context, env override lebih dulu."""
        env_branch = os.getenv("CODEGUARD_DEFAULT_BRANCH")
        if env_branch:
            print(f"[GitHubClient] Default branch override: {env_branch}")
            return env_branch

        try:
            response = httpx.get(self.base_url, headers=self.headers, timeout=10)
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

            print(f"[GitHubClient] Failed to get default branch: HTTP {response.status_code}")
        except Exception as e:
            print(f"[GitHubClient] Error getting default branch: {e}")

        return os.getenv("CODEGUARD_DEFAULT_BRANCH", "main")

    def get_open_pr_for_branch(self, branch: str, head_owner: str | None = None) -> int | None:
        """
        Cari PR yang open untuk branch tertentu.
        Return PR number kalau ada, None kalau tidak ada.
        """
        owner = head_owner or self.owner
        url = f"{self.base_url}/pulls"
        try:
            response = httpx.get(
                url,
                headers=self.headers,
                params={"state": "open", "head": f"{owner}:{branch}"},
                timeout=10,
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
                print(f"[GitHubClient] Failed to get PRs: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"[GitHubClient] Error: {e}")
            return None

    def post_pr_comment(self, pr_number: int, body: str) -> bool:
        """
        Post comment ke PR.
        Return True kalau berhasil.
        """
        url = f"{self.base_url}/issues/{pr_number}/comments"
        payload = {"body": body}
        try:
            response = httpx.post(url, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 201:
                print(f"[GitHubClient] Comment posted to PR #{pr_number} ✅")
                return True
            else:
                print(f"[GitHubClient] Failed to post comment: HTTP {response.status_code}")
                print(response.text[:200])
                return False
        except Exception as e:
            print(f"[GitHubClient] Error: {e}")
            return False

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> bool:
        """
        Buat GitHub Issue — fallback kalau tidak ada PR, atau entry point
        untuk Sentry bug analysis.
        labels default ["codeguard-ai"] kalau tidak di-pass -- perilaku
        lama (dipanggil dari process_github_review) tidak berubah.
        """
        url = f"{self.base_url}/issues"
        payload = {
            "title": title,
            "body": body,
            "labels": labels if labels is not None else ["codeguard-ai"],
        }
        try:
            response = httpx.post(url, headers=self.headers, json=payload, timeout=10)
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
                print(f"[GitHubClient] Failed to create issue: HTTP {response.status_code}")
                return False
        except Exception as e:
            print(f"[GitHubClient] Error: {e}")
            return False
