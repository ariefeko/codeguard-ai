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
