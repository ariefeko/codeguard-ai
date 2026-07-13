import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote
import re
import base64
import os
import httpx
from src.config import HTTP_REQUEST_TIMEOUT_SECONDS, SUPPORTED_EXTENSIONS, SKIP_DIRS
from src.github.http_client import build_github_headers, get_github_http_client


logger = logging.getLogger(__name__)

# Regex import per bahasa
IMPORT_PATTERNS = {
    ".php": [
        r"use\s+([\w\\]+);",
        r"require(?:_once)?\s+['\"](.+?)['\"]",
        r"include(?:_once)?\s+['\"](.+?)['\"]",
    ],
    ".py": [
        r"from\s+([\w.]+)\s+import",
        r"^import\s+([\w.]+)",
    ],
    ".js": [
        r"import\s+.*?from\s+['\"](.+?)['\"]",
        r"require\(['\"](.+?)['\"]\)",
    ],
    ".ts": [
        r"import\s+.*?from\s+['\"](.+?)['\"]",
        r"require\(['\"](.+?)['\"]\)",
    ],
    ".java": [
        r"import\s+([\w.]+);",
    ],
    ".go": [
        r"\"([\w./]+)\"",
    ],
    ".cs": [
        r"using\s+([\w.]+);",
    ],
    ".razor": [
        r"@using\s+([\w.]+)",
        r"@inject\s+\w+\s+([\w.]+)",
    ],
    ".cshtml": [
        r"@using\s+([\w.]+)",
    ],
    ".twig": [
        r"{%\s*include\s+['\"](.+?)['\"]",
        r"{%\s*extends\s+['\"](.+?)['\"]",
        r"{%\s*import\s+['\"](.+?)['\"]",
    ],
    ".h": [
        r"#include\s+[\"<](.+?)[\">]",
    ],
    ".hpp": [
        r"#include\s+[\"<](.+?)[\">]",
    ],
    ".cpp": [
        r"#include\s+[\"<](.+?)[\">]",
    ],
    ".cc": [
        r"#include\s+[\"<](.+?)[\">]",
    ],
    ".cxx": [
        r"#include\s+[\"<](.+?)[\">]",
    ],
}


class ContextBuilder:
    def __init__(
        self,
        owner: str,
        repo: str,
        ref: str,
        http_client: httpx.Client | None = None,
    ):
        """
        owner : GitHub username, e.g. "ariefeko"
        repo  : repo name, e.g. "tagihin"
        ref   : commit SHA atau branch, e.g. "abc123" atau "develop"
        """
        self.owner = owner
        self.repo = repo
        self.ref = ref
        self.token = os.getenv("GITHUB_PAT_TOKEN")
        self.headers = build_github_headers(self.token)
        self.http_client = http_client or get_github_http_client()
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
        self._repo_tree_cache = None  # cache tree agar tidak fetch berulang

    def build(self, changed_files: list[str]) -> dict:
        """
        Entry point utama.
        Input : list path file yang berubah (dari webhook)
        Output: dict siap kirim ke LLM
        """
        analyzable = self._filter(changed_files)

        print(f"[ContextBuilder] Analyzable files: {analyzable}")

        changed_contents = self._fetch_files(analyzable)

        related_contents = {}
        for file_path in analyzable:
            content = changed_contents.get(file_path, "")
            related = self.find_related_files(file_path, content)
            for r in related:
                if r not in changed_contents and r not in related_contents:
                    fetched = self._fetch_file(r)
                    if fetched:
                        related_contents[r] = fetched

        return {
            "changed_files": changed_contents,
            "related_files": related_contents,
        }

    def _filter(self, files: list[str]) -> list[str]:
        """Buang file yang tidak perlu dianalisis."""
        result = []
        for f in files:
            if not isinstance(f, str) or not f or "\x00" in f:
                logger.warning("Rejected invalid repository path")
                continue

            normalized = f.replace("\\", "/")
            decoded = normalized
            for _ in range(2):
                decoded = unquote(decoded)
            posix_path = PurePosixPath(decoded.replace("\\", "/"))
            windows_path = PureWindowsPath(decoded)
            if (
                posix_path.is_absolute()
                or windows_path.is_absolute()
                or bool(windows_path.drive)
                or bool(windows_path.root)
                or ".." in posix_path.parts
            ):
                logger.warning("Rejected suspicious repository path")
                continue

            path = Path(normalized)
            if path.suffix not in SUPPORTED_EXTENSIONS:
                continue
            if ".blade." in path.name:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            result.append(normalized)
        return result

    def _fetch_file(self, file_path: str) -> str | None:
        """Fetch satu file dari GitHub API, return isi sebagai string."""
        url = f"{self.base_url}/{file_path}?ref={self.ref}"
        try:
            response = self.http_client.get(
                url,
                headers=self.headers,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                data = response.json()
                content_b64 = data.get("content", "")
                return base64.b64decode(content_b64).decode("utf-8")
            else:
                print(f"[ContextBuilder] Failed to fetch {file_path}: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"[ContextBuilder] Error fetching {file_path}: {e}")
            return None

    def _fetch_files(self, files: list[str]) -> dict:
        """Fetch banyak file sekaligus."""
        result = {}
        for f in files:
            content = self._fetch_file(f)
            if content:
                result[f] = content
        return result

    def _get_repo_tree(self) -> list:
        """Fetch repo tree sekali, cache untuk reuse."""
        if self._repo_tree_cache is not None:
            return self._repo_tree_cache

        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{self.ref}?recursive=1"
        try:
            response = self.http_client.get(
                url,
                headers=self.headers,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                self._repo_tree_cache = response.json().get("tree", [])
                return self._repo_tree_cache
            else:
                print(f"[ContextBuilder] Failed to fetch repo tree: HTTP {response.status_code}")
                return []
        except Exception as e:
            print(f"[ContextBuilder] Error fetching repo tree: {e}")
            return []

    def extract_dependencies(self, file_path: str, content: str) -> list[str]:
        """Extract import/require dari content file."""
        path = Path(file_path)
        patterns = IMPORT_PATTERNS.get(path.suffix, [])
        if not patterns or not content:
            return []

        deps = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            deps.extend(matches)

        return deps

    def find_related_files(self, file_path: str, content: str) -> list[str]:
        """Dari import yang ditemukan, resolve ke path file di repo."""
        deps = self.extract_dependencies(file_path, content)
        path = Path(file_path)
        related = []

        for dep in deps:
            resolved = self._resolve_dep(dep, path)
            if resolved:
                related.append(resolved)

        return related

    def _resolve_dep(self, dep: str, source_file: Path) -> str | None:
        """Resolve dependency string ke path file aktual."""
        suffix = source_file.suffix

        if suffix == ".php":
            class_name = dep.split("\\")[-1]
            return self._search_file_in_tree(class_name, ".php")

        elif suffix == ".py":
            filename = dep.split(".")[-1]
            return self._search_file_in_tree(filename, ".py")

        elif suffix in (".js", ".ts"):
            name = Path(dep).name
            for ext in (".js", ".ts"):
                found = self._search_file_in_tree(name, ext)
                if found:
                    return found

        elif suffix == ".java":
            class_name = dep.split(".")[-1]
            return self._search_file_in_tree(class_name, ".java")

        elif suffix == ".go":
            if not dep.startswith(("fmt", "os", "net", "strings", "github.com")):
                name = Path(dep).name
                return self._search_file_in_tree(name, ".go")

        elif suffix in (".cs", ".razor", ".cshtml"):
            class_name = dep.split(".")[-1]
            for ext in (".cs", ".razor", ".cshtml"):
                found = self._search_file_in_tree(class_name, ext)
                if found:
                    return found

        return None

    def _search_file_in_tree(self, name: str, ext: str) -> str | None:
        """Cari file by nama di repo tree (pakai cache)."""
        tree = self._get_repo_tree()
        target = f"{name}{ext}"

        for item in tree:
            item_path = item.get("path", "")
            if item_path.endswith(target):
                if not any(part in SKIP_DIRS for part in Path(item_path).parts):
                    return item_path

        return None
