from pathlib import Path
import re
from src.config import SUPPORTED_EXTENSIONS, SKIP_DIRS

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
}


class ContextBuilder:
    def __init__(self, project_path: str):
        self.project_path = Path(project_path)

    def build(self, changed_files: list[str]) -> dict:
        """
        Entry point utama.
        Input : list path file yang berubah (dari webhook)
        Output: dict siap kirim ke LLM
        """
        analyzable = self._filter(changed_files)

        changed_contents = self._read_files(analyzable)

        related_contents = {}
        for file_path in analyzable:
            related = self.find_related_files(file_path)
            for r in related:
                if r not in changed_contents and r not in related_contents:
                    content = self._read_file(r)
                    if content:
                        related_contents[r] = content

        return {
            "changed_files": changed_contents,
            "related_files": related_contents,
        }

    def _filter(self, files: list[str]) -> list[str]:
        """Buang file yang tidak perlu dianalisis."""
        result = []
        for f in files:
            path = Path(f)
            if path.suffix not in SUPPORTED_EXTENSIONS:
                continue
            if ".blade." in path.name:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            result.append(f)
        return result

    def _read_file(self, file_path: str) -> str | None:
        """Baca satu file."""
        try:
            full_path = self.project_path / file_path
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[ContextBuilder] Failed to read {file_path}: {e}")
            return None

    def _read_files(self, files: list[str]) -> dict:
        """Baca banyak file sekaligus."""
        result = {}
        for f in files:
            content = self._read_file(f)
            if content:
                result[f] = content
        return result

    def extract_dependencies(self, file_path: str) -> list[str]:
        """Extract import/require dari satu file."""
        path = Path(file_path)
        patterns = IMPORT_PATTERNS.get(path.suffix, [])
        if not patterns:
            return []

        content = self._read_file(file_path)
        if not content:
            return []

        deps = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            deps.extend(matches)

        return deps

    def find_related_files(self, file_path: str) -> list[str]:
        """
        Dari import yang ditemukan, resolve ke path file
        yang benar-benar ada di project.
        """
        deps = self.extract_dependencies(file_path)
        path = Path(file_path)
        related = []

        for dep in deps:
            resolved = self._resolve_dep(dep, path)
            if resolved:
                related.append(resolved)

        return related

    def _resolve_dep(self, dep: str, source_file: Path) -> str | None:
        """
        Resolve dependency string ke path file aktual.
        """
        suffix = source_file.suffix

        if suffix == ".php":
            class_name = dep.split("\\")[-1]
            return self._find_file_by_name(class_name, ".php")

        elif suffix == ".py":
            filename = dep.split(".")[-1]
            return self._find_file_by_name(filename, ".py")

        elif suffix in (".js", ".ts"):
            name = Path(dep).name
            for ext in (".js", ".ts"):
                found = self._find_file_by_name(name, ext)
                if found:
                    return found

        elif suffix == ".java":
            class_name = dep.split(".")[-1]
            return self._find_file_by_name(class_name, ".java")

        elif suffix == ".go":
            if not dep.startswith(("fmt", "os", "net", "strings", "github.com")):
                name = Path(dep).name
                return self._find_file_by_name(name, ".go")

        elif suffix in (".cs", ".razor", ".cshtml"):
            class_name = dep.split(".")[-1]
            for ext in (".cs", ".razor", ".cshtml"):
                found = self._find_file_by_name(class_name, ext)
                if found:
                    return found

        return None

    def _find_file_by_name(self, name: str, ext: str) -> str | None:
        """Cari file by nama di seluruh project."""
        target = f"{name}{ext}"
        for file in self.project_path.rglob(target):
            if not any(part in SKIP_DIRS for part in file.parts):
                return str(file.relative_to(self.project_path))
        return None
