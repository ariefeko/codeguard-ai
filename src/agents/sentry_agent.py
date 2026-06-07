from pathlib import Path
from src.config import SUPPORTED_EXTENSIONS, SKIP_DIRS, SKIP_FILES


class SentryAgent:

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)

    def collect_files(self):
        files = []

        for file in self.project_path.rglob("*"):
            if any(part in SKIP_DIRS for part in file.parts):
                continue

            if file.name in SKIP_FILES:
                continue

            if (
                file.is_file()
                and file.suffix in SUPPORTED_EXTENSIONS
            ):
                files.append(file)

        return files

    def read_file(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return file.read()
        except Exception as e:
            print(f"Failed to read {file_path}: {e}")
            return None

    def read_files(self):
        files = self.collect_files()
        results = {}

        for file in files:
            content = self.read_file(file)
            if content:
                results[str(file)] = content

        return results


if __name__ == "__main__":
    agent = SentryAgent(".")
    files_content = agent.read_files()
    print(f"Found {len(files_content)} source files\n")
    for path in files_content:
        print(path)
