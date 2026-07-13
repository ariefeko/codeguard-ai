import os


def read_positive_float_env(name: str, default: float) -> float:
    """Read a positive numeric setting, falling back safely when invalid."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


HTTP_REQUEST_TIMEOUT_SECONDS = read_positive_float_env(
    "HTTP_REQUEST_TIMEOUT_SECONDS",
    10.0,
)
LLM_REQUEST_TIMEOUT_SECONDS = read_positive_float_env(
    "LLM_REQUEST_TIMEOUT_SECONDS",
    60.0,
)
QDRANT_SYNC_TIMEOUT_SECONDS = read_positive_float_env(
    "QDRANT_SYNC_TIMEOUT_SECONDS",
    30.0,
)


SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".php",
    ".java",
    ".go",
    ".cs",
    ".razor",
    ".cshtml",
    ".twig",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
}

SKIP_EXTENSIONS = {
    ".md", ".env", ".sql", ".lock", ".json",
    ".yaml", ".yml", ".toml", ".txt", ".log",
    ".csv", ".xml", ".html", ".blade.php",
    ".csproj", ".sln", ".aspx",
}

SKIP_DIRS = {
    "node_modules", "vendor", "core", ".git",
    ".venv", "__pycache__", "dist", "build",
    "coverage", ".next", ".nuxt", "migrations",
    "storage", "bootstrap/cache",
}

SKIP_FILES = {
    "__init__.py",
    "conftest.py",
}
