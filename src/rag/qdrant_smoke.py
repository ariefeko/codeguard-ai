import argparse
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.rag.rag_pipeline import RAGPipeline


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    message: str
    snippet_count: int = 0


def run_smoke(
    context: dict | None = None,
    require_api_key: bool = True,
) -> SmokeResult:
    errors = _validate_env(require_api_key=require_api_key)
    if errors:
        return SmokeResult(
            ok=False,
            message=f"Invalid smoke environment: {', '.join(errors)}",
        )

    pipeline = RAGPipeline(enabled=True)
    smoke_context = context or _default_context()
    selection = pipeline.topic_mapper.from_context(smoke_context)

    try:
        available_collections = pipeline.client.list_collections()
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code is None:
            message = type(exc).__name__
        else:
            message = f"{type(exc).__name__}(status_code={status_code})"
        return SmokeResult(ok=False, message=f"Qdrant connection failed: {message}")

    if not available_collections:
        return SmokeResult(
            ok=False,
            message=(
                "Qdrant connection succeeded but no collections are indexed yet. "
                f"Expected one of: {', '.join(selection.collections)}"
            ),
        )

    missing_collections = [
        collection
        for collection in selection.collections
        if collection not in available_collections
    ]
    if len(missing_collections) == len(selection.collections):
        return SmokeResult(
            ok=False,
            message=(
                "Qdrant connection succeeded but expected RAG collections are missing. "
                f"Expected one of: {', '.join(selection.collections)}; "
                f"available: {', '.join(available_collections)}"
            ),
        )

    snippets = pipeline.retrieve(selection)
    if pipeline.last_query_error:
        return SmokeResult(
            ok=False,
            message=f"Qdrant query failed: {pipeline.last_query_error}",
        )

    if not snippets:
        return SmokeResult(
            ok=False,
            message="Qdrant query completed but returned no matching RAG snippets.",
        )

    topics = ", ".join(snippet.topic for snippet in snippets)
    return SmokeResult(
        ok=True,
        message=f"Qdrant smoke query returned topics: {topics}",
        snippet_count=len(snippets),
    )


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run a read-only smoke query against the configured Qdrant RAG store."
    )
    parser.add_argument(
        "--allow-missing-api-key",
        action="store_true",
        help="Allow local Qdrant instances that do not require QDRANT_API_KEY.",
    )
    args = parser.parse_args()

    result = run_smoke(require_api_key=not args.allow_missing_api_key)
    status = "ok" if result.ok else "failed"
    print(
        "[RAG] "
        f"event=rag_qdrant_smoke_{status} "
        f"snippet_count={result.snippet_count} "
        f"message={result.message}"
    )
    return 0 if result.ok else 1


def _validate_env(require_api_key: bool) -> list[str]:
    required = ["QDRANT_URL", "RAG_ENABLED"]
    if require_api_key:
        required.append("QDRANT_API_KEY")

    errors = [
        f"{name} is missing"
        for name in required
        if not os.getenv(name, "").strip()
    ]
    rag_enabled = os.getenv("RAG_ENABLED", "").strip().lower()
    if rag_enabled and rag_enabled not in {"1", "true", "yes", "on"}:
        errors.append("RAG_ENABLED must be true")
    return errors


def _default_context() -> dict:
    return {
        "changed_files": {
            "app/Http/Controllers/UserController.php": (
                "Laravel controller builds raw sql from request input."
            )
        },
        "related_files": {},
    }


if __name__ == "__main__":
    raise SystemExit(main())
