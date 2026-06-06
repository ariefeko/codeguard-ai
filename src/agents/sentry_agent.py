from pathlib import Path

IGNORE_DIRS = {
    "node_modules",
    "vendor",
    "core",
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
}

IGNORE_FILES = {
    "__init__.py",
    "conftest.py",
}

SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".php",
    ".java",
    ".go",
}


class SentryAgent:

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        

    def collect_files(self):
        files = []

        for file in self.project_path.rglob("*"):
            if any(part in IGNORE_DIRS for part in file.parts):
                continue
                
            if file.name in IGNORE_FILES:
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
    
    
    def extract_dependencies(self, file_path):
        pass
    
    
    def find_related_files(self, target_file):
        pass


if __name__ == "__main__":
    agent = SentryAgent(".")

    files = agent.collect_files()

    # print(f"Found {len(files)} source files")
    
    # for file in files:
    #     print(f"\n=== {file} ===\n")

    #     content = agent.read_file(file)

    #     # print(content[:300])
    files_content = agent.read_files()
    print(f"Found {len(files_content)} source files\n")

    for path in files_content:
        print(path)
