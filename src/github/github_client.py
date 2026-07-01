import os
import httpx


class GitHubClient:
    def __init__(self, owner: str, repo: str):
        self.owner = owner
        self.repo = repo
        self.token = os.getenv("GITHUB_PAT_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

    def get_default_branch(self) -> str:
        """Ambil branch target untuk Sentry context, env override lebih dulu."""
        env_branch = os.getenv("CODEGUARD_DEFAULT_BRANCH")
        if env_branch:
            print(f"[GitHubClient] Default branch override: {env_branch}")
            return env_branch

        try:
            response = httpx.get(self.base_url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                default_branch = response.json().get("default_branch")
                if default_branch:
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
                prs = response.json()
                if prs:
                    pr_number = prs[0]["number"]
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
                issue_url = response.json().get("html_url")
                print(f"[GitHubClient] Issue created: {issue_url} ✅")
                return True
            else:
                print(f"[GitHubClient] Failed to create issue: HTTP {response.status_code}")
                return False
        except Exception as e:
            print(f"[GitHubClient] Error: {e}")
            return False
