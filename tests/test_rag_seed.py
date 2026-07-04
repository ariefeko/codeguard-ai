from src.rag.knowledge_base import (
    documents_for_selection,
    load_seed_documents,
    validate_seed_documents,
)
from src.rag.topic_mapper import TopicMapper


def test_mvp_seed_schema_is_valid():
    documents = load_seed_documents()

    assert validate_seed_documents(documents) == []


def test_mvp_seed_covers_php_python_and_nodejs():
    documents = load_seed_documents()
    languages = {document["metadata"]["language"] for document in documents}
    frameworks = {document["metadata"]["framework"] for document in documents}

    assert {"php", "python", "js"}.issubset(languages)
    assert {"laravel", "fastapi", "express"}.issubset(frameworks)


def test_mvp_seed_matches_common_mapper_selections():
    mapper = TopicMapper()
    documents = load_seed_documents()
    contexts = [
        {
            "changed_files": {
                "app/Http/Controllers/UserController.php": (
                    "Laravel controller builds raw sql from request input."
                )
            },
            "related_files": {},
        },
        {
            "changed_files": {
                "src/api/main.py": (
                    "FastAPI service uses dependency injection for request handlers."
                )
            },
            "related_files": {},
        },
        {
            "changed_files": {
                "src/routes/users.ts": (
                    "Express route delegates user creation to middleware."
                )
            },
            "related_files": {},
        },
    ]

    for context in contexts:
        selection = mapper.from_context(context)

        assert documents_for_selection(selection, documents)


def test_mvp_seed_matches_laravel_sentry_not_found_selection():
    mapper = TopicMapper()
    documents = load_seed_documents()
    context = {
        "changed_files": {
            "app/Models/BillingAccount.php": "Laravel model lookup can miss records."
        },
        "related_files": {},
    }
    error = {
        "type": "ModelNotFoundException",
        "message": "No query results for model",
        "file": "app/Models/BillingAccount.php",
    }

    selection = mapper.from_error(error, context)

    assert documents_for_selection(selection, documents)
