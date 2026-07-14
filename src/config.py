import os


CODEGUARD_APP_ID = "codeguard-ai"
CODEGUARD_REPOSITORY_URL = f"https://github.com/ariefeko/{CODEGUARD_APP_ID}"
GITHUB_STATUS_DESCRIPTION_MAX_LENGTH = 140
DEFAULT_REPOSITORY_BRANCH = "main"


def read_positive_float_env(name: str, default: float) -> float:
    """Read a positive numeric setting, falling back safely when invalid."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def read_positive_int_env(name: str, default: int) -> int:
    """Read a positive integer setting, falling back safely when invalid."""
    try:
        value = int(os.getenv(name, str(default)))
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
GITHUB_RETRY_MAX_ATTEMPTS = read_positive_int_env(
    "GITHUB_RETRY_MAX_ATTEMPTS",
    3,
)
GITHUB_RETRY_BASE_DELAY_SECONDS = read_positive_float_env(
    "GITHUB_RETRY_BASE_DELAY_SECONDS",
    0.5,
)
GITHUB_RETRY_MAX_DELAY_SECONDS = read_positive_float_env(
    "GITHUB_RETRY_MAX_DELAY_SECONDS",
    4.0,
)
REDIS_RETRY_ATTEMPTS = read_positive_int_env(
    "REDIS_RETRY_ATTEMPTS",
    3,
)
REDIS_RETRY_BASE_DELAY_SECONDS = read_positive_float_env(
    "REDIS_RETRY_BASE_DELAY_SECONDS",
    0.1,
)
REDIS_RETRY_MAX_DELAY_SECONDS = read_positive_float_env(
    "REDIS_RETRY_MAX_DELAY_SECONDS",
    2.0,
)
RQ_JOB_TIMEOUT_SECONDS = read_positive_int_env(
    "RQ_JOB_TIMEOUT_SECONDS",
    120,
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
