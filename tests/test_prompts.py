from src.orchestration.prompts import build_bug_fix_prompt, build_code_review_prompt


def test_code_review_prompt_separates_rag_from_latest_references():
    context = {
        "changed_files": {"app/Http/Controllers/UserController.php": "changed content"},
        "related_files": {},
    }

    prompt = build_code_review_prompt(
        context,
        {
            "rag": "Relevant curated knowledge:\n1. [owasp_sql_injection] Use bindings.",
            "owasp_top10": "Latest OWASP reference",
        },
    )

    assert "=== CURATED RAG KNOWLEDGE ===" in prompt
    assert "Relevant curated knowledge:" in prompt
    assert "=== LATEST SECURITY & BEST PRACTICE REFERENCES ===" in prompt
    assert "[OWASP TOP10]" in prompt
    assert "[RAG]" not in prompt


def test_bug_fix_prompt_separates_rag_from_latest_references():
    context = {
        "changed_files": {"src/app.py": "changed content"},
        "related_files": {},
    }
    error = {
        "type": "RuntimeError",
        "message": "boom",
        "file": "src/app.py",
        "line": 1,
    }

    prompt = build_bug_fix_prompt(
        context,
        error,
        {
            "rag": "Relevant curated knowledge:\n1. [missing_null_handling] Guard nulls.",
            "error_info": "Latest RuntimeError reference",
        },
    )

    assert "=== CURATED RAG KNOWLEDGE ===" in prompt
    assert "missing_null_handling" in prompt
    assert "=== LATEST REFERENCES ===" in prompt
    assert "[ERROR INFO]" in prompt
    assert "[RAG]" not in prompt
