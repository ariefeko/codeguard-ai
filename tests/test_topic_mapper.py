from src.rag.topic_mapper import (
    CATEGORY_CODE_QUALITY,
    CATEGORY_SECURITY,
    TopicMapper,
)


def test_maps_laravel_sql_injection_context_to_security_topics():
    mapper = TopicMapper()
    context = {
        "changed_files": {
            "app/Http/Controllers/UserController.php": (
                "<?php\n"
                "use Illuminate\\Support\\Facades\\DB;\n"
                "$users = DB::raw('select * from users where email = ' . $email);"
            )
        },
        "related_files": {},
    }

    result = mapper.from_context(context)

    assert result.language == "php"
    assert result.framework == "laravel"
    assert result.category == CATEGORY_SECURITY
    assert "owasp_sql_injection" in result.topics
    assert "laravel_query_builder_security" in result.topics
    assert result.collections == ("security_php", "security_general")


def test_maps_fastapi_context_without_external_dependencies():
    mapper = TopicMapper()
    context = {
        "changed_files": {
            "src/api/main.py": (
                "from fastapi import FastAPI, Depends\n"
                "app = FastAPI()\n"
            )
        },
        "related_files": {},
    }

    result = mapper.from_context(context)

    assert result.language == "python"
    assert result.framework == "fastapi"
    assert "fastapi_security_basics" in result.topics
    assert "fastapi_dependency_injection" in result.topics
    assert result.collections == ("bestpractice_python", "quality_general")


def test_maps_express_typescript_context():
    mapper = TopicMapper()
    context = {
        "changed_files": {
            "src/routes/users.ts": (
                "import express from 'express';\n"
                "const router = express.Router();\n"
                "router.post('/users', handler);\n"
            )
        },
        "related_files": {},
    }

    result = mapper.from_context(context)

    assert result.language == "js"
    assert result.framework == "express"
    assert "express_security_basics" in result.topics
    assert "express_middleware_patterns" in result.topics
    assert result.collections == ("bestpractice_js", "quality_general")


def test_unknown_context_falls_back_to_general_quality_topics():
    mapper = TopicMapper()
    context = {
        "changed_files": {"README.md": "# docs"},
        "related_files": {},
    }

    result = mapper.from_context(context)

    assert result.language == "unknown"
    assert result.framework == "unknown"
    assert result.category == CATEGORY_CODE_QUALITY
    assert result.topics == (
        "secure_coding_basics",
        "code_review_best_practices",
    )
    assert result.collections == ("quality_general",)


def test_parent_category_validation_moves_security_topic_to_security():
    mapper = TopicMapper()

    result = mapper._build_selection(
        language="php",
        framework="laravel",
        category=CATEGORY_CODE_QUALITY,
        topics=("owasp_sql_injection",),
        source="github_pr",
    )

    assert result.category == CATEGORY_SECURITY
    assert result.collections == ("security_php", "security_general")


def test_maps_laravel_model_not_found_error_to_quality_and_framework_topics():
    mapper = TopicMapper()
    context = {
        "changed_files": {
            "app/Services/BillingService.php": (
                "<?php\n"
                "use Illuminate\\Database\\Eloquent\\Model;\n"
            )
        },
        "related_files": {},
    }
    error = {
        "type": "ModelNotFoundException",
        "message": "No query results for model",
        "file": "app/Services/BillingService.php",
        "line": 87,
    }

    result = mapper.from_error(error, context)

    assert result.source == "sentry"
    assert result.language == "php"
    assert result.framework == "laravel"
    assert result.category == CATEGORY_CODE_QUALITY
    assert "missing_null_handling" in result.topics
    assert "laravel_exception_handling" in result.topics
    assert result.collections == ("quality_general", "bestpractice_php")
