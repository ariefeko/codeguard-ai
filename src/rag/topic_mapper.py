from collections import Counter
from dataclasses import dataclass
from pathlib import Path


CATEGORY_SECURITY = "security"
CATEGORY_BEST_PRACTICE = "best_practice"
CATEGORY_CODE_QUALITY = "code_quality"
CATEGORY_PERFORMANCE = "performance"
CATEGORY_FRAMEWORK = "framework"

GENERAL_FALLBACK_TOPICS = (
    "secure_coding_basics",
    "code_review_best_practices",
)

EXTENSION_LANGUAGE = {
    ".php": "php",
    ".py": "python",
    ".js": "js",
    ".ts": "js",
    ".jsx": "js",
    ".tsx": "js",
    ".java": "java",
    ".go": "go",
    ".cs": "csharp",
    ".razor": "csharp",
    ".cshtml": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
}

LANGUAGE_COLLECTION_SUFFIX = {
    "php": "php",
    "python": "python",
    "js": "js",
    "java": "java",
    "go": "go",
}

TOPIC_PARENT_CATEGORY = {
    "owasp_sql_injection": CATEGORY_SECURITY,
    "laravel_query_builder_security": CATEGORY_SECURITY,
    "php_pdo_prepared_statement": CATEGORY_SECURITY,
    "laravel_authorization": CATEGORY_SECURITY,
    "laravel_csrf_protection": CATEGORY_SECURITY,
    "xss_output_escaping": CATEGORY_SECURITY,
    "safe_error_reporting": CATEGORY_SECURITY,
    "fastapi_security_basics": CATEGORY_SECURITY,
    "express_security_basics": CATEGORY_SECURITY,
    "spring_security_basics": CATEGORY_SECURITY,
    "secure_coding_basics": CATEGORY_SECURITY,
    "react_security_basics": CATEGORY_SECURITY,
    "react_xss_dangerously_set_innerhtml": CATEGORY_SECURITY,
    "react_unsafe_url_injection": CATEGORY_SECURITY,
    "react_eval_injection": CATEGORY_SECURITY,
    "react_client_secret_exposure": CATEGORY_SECURITY,
    "tanstack_query_cache_poisoning": CATEGORY_SECURITY,
    "tanstack_query_unvalidated_params": CATEGORY_SECURITY,
    "laravel_validation": CATEGORY_BEST_PRACTICE,
    "eloquent_best_practices": CATEGORY_BEST_PRACTICE,
    "fastapi_dependency_injection": CATEGORY_BEST_PRACTICE,
    "express_middleware_patterns": CATEGORY_BEST_PRACTICE,
    "spring_boot_configuration": CATEGORY_BEST_PRACTICE,
    "go_error_handling": CATEGORY_BEST_PRACTICE,
    "react_best_practices": CATEGORY_BEST_PRACTICE,
    "tanstack_query_best_practices": CATEGORY_BEST_PRACTICE,
    "missing_null_handling": CATEGORY_CODE_QUALITY,
    "laravel_exception_handling": CATEGORY_FRAMEWORK,
    "code_review_best_practices": CATEGORY_CODE_QUALITY,
}


@dataclass(frozen=True)
class TopicSelection:
    language: str
    framework: str
    category: str
    topics: tuple[str, ...]
    collections: tuple[str, ...]
    source: str


class TopicMapper:
    def from_context(self, context: dict) -> TopicSelection:
        documents = self._collect_documents(context)
        language = self._detect_language(documents)
        framework = self._detect_framework(documents, language)
        category, topics = self._topics_from_documents(documents, language, framework)

        return self._build_selection(
            language=language,
            framework=framework,
            category=category,
            topics=topics,
            source="github_pr",
        )

    def from_error(self, error: dict, context: dict | None = None) -> TopicSelection:
        documents = self._collect_documents(context or {})
        error_file = error.get("file")
        if error_file:
            documents.setdefault(error_file, "")

        language = self._detect_language(documents)
        framework = self._detect_framework(documents, language)
        category, topics = self._topics_from_error(error, language, framework)

        return self._build_selection(
            language=language,
            framework=framework,
            category=category,
            topics=topics,
            source="sentry",
        )

    def collections_for(self, category: str, language: str) -> tuple[str, ...]:
        return self._collections_for(category, language)

    def _collect_documents(self, context: dict) -> dict[str, str]:
        documents = {}
        documents.update(context.get("related_files", {}))
        documents.update(context.get("changed_files", {}))
        return {path: content or "" for path, content in documents.items()}

    def _detect_language(self, documents: dict[str, str]) -> str:
        languages = []
        for path in documents:
            suffix = Path(path).suffix.lower()
            language = EXTENSION_LANGUAGE.get(suffix)
            if language:
                languages.append(language)

        if not languages:
            return "unknown"

        return Counter(languages).most_common(1)[0][0]

    def _detect_framework(self, documents: dict[str, str], language: str) -> str:
        searchable = "\n".join(
            [path.lower() for path in documents]
            + [content.lower() for content in documents.values()]
        )

        if language == "php" and self._has_any(
            searchable,
            (
                "illuminate\\",
                "illuminate/",
                "route::",
                "eloquent",
                "app/http/",
                "app/models/",
                "routes/web.php",
                "routes/api.php",
                "artisan",
            ),
        ):
            return "laravel"

        if language == "python" and self._has_any(
            searchable,
            (
                "fastapi",
                "apirouter",
                "depends(",
                "from fastapi",
            ),
        ):
            return "fastapi"

        if language == "js" and self._has_any(searchable, (".jsx", ".tsx")):
            return "react"

        if language == "js" and self._has_any(
            searchable,
            (
                "from \"react\"",
                "from 'react'",
                "react-dom",
                "usestate(",
                "useeffect(",
                "usecontext(",
                "usememo(",
                "usecallback(",
            ),
        ):
            return "react"

        if language == "js" and self._has_any(
            searchable,
            (
                "express",
                "app.get(",
                "app.post(",
                "router.get(",
                "router.post(",
            ),
        ):
            return "express"

        if language == "java" and self._has_any(
            searchable,
            (
                "@springbootapplication",
                "@restcontroller",
                "org.springframework",
            ),
        ):
            return "spring"

        return "unknown"

    def _topics_from_documents(
        self,
        documents: dict[str, str],
        language: str,
        framework: str,
    ) -> tuple[str, tuple[str, ...]]:
        searchable = "\n".join(documents.values()).lower()
        paths = "\n".join(path.lower() for path in documents)
        topics = []
        category = CATEGORY_BEST_PRACTICE

        if self._has_any(
            searchable,
            (
                "whereraw",
                "selectraw",
                "db::raw",
                "raw sql",
                "execute(",
                "cursor.execute",
            ),
        ):
            category = CATEGORY_SECURITY
            topics.extend(
                (
                    "owasp_sql_injection",
                    "laravel_query_builder_security"
                    if framework == "laravel"
                    else "php_pdo_prepared_statement",
                )
            )

        if self._has_any(searchable + paths, ("csrf", "x-csrf-token")):
            category = CATEGORY_SECURITY
            topics.append("laravel_csrf_protection")

        if self._has_any(searchable, ("{!!", "innerhtml", "v-html")):
            category = CATEGORY_SECURITY
            topics.append("xss_output_escaping")

        if language == "js" and framework == "react":
            if self._has_any(searchable, ("dangerouslysetinnerhtml",)):
                category = CATEGORY_SECURITY
                topics.append("react_xss_dangerously_set_innerhtml")

            if self._has_any(
                searchable,
                (
                    "eval(",
                    "new function(",
                    "settimeout(\"",
                    "settimeout('",
                    "setinterval(\"",
                    "setinterval('",
                ),
            ):
                category = CATEGORY_SECURITY
                topics.append("react_eval_injection")

            if self._has_any(
                searchable,
                (
                    "javascript:",
                    "document.write(",
                    "window.location = ",
                    "window.location.href = ",
                ),
            ):
                category = CATEGORY_SECURITY
                topics.append("react_unsafe_url_injection")

            if self._has_any(
                searchable,
                ("next_public_", "vite_"),
            ) and self._has_any(
                searchable,
                ("secret", "api_key", "private_key", "password", "token"),
            ):
                category = CATEGORY_SECURITY
                topics.append("react_client_secret_exposure")

            if self._has_any(
                searchable,
                ("usequery(", "usemutation(", "@tanstack/react-query", "queryclient"),
            ):
                if self._has_any(
                    searchable,
                    ("token", "session", "auth", "user_id", "userid"),
                ):
                    category = CATEGORY_SECURITY
                    topics.append("tanstack_query_cache_poisoning")

                # Interpolating raw request/user input directly into a fetch URL or
                # query key (instead of passing it as a controlled argument) risks
                # SSRF/path traversal and cache-key collisions across users.
                if self._has_any(searchable, ("usequery(`", "fetch(`", "axios.get(`")):
                    category = CATEGORY_SECURITY
                    topics.append("tanstack_query_unvalidated_params")

        if language == "php" and framework == "laravel":
            topics.extend(("laravel_validation", "eloquent_best_practices"))
        elif language == "python" and framework == "fastapi":
            topics.extend(("fastapi_security_basics", "fastapi_dependency_injection"))
        elif language == "js" and framework == "react":
            topics.extend(("react_security_basics", "react_best_practices"))
            if self._has_any(searchable, ("usequery(", "usemutation(", "@tanstack/react-query")):
                topics.append("tanstack_query_best_practices")
        elif language == "js" and framework == "express":
            topics.extend(("express_security_basics", "express_middleware_patterns"))
        elif language == "java" and framework == "spring":
            topics.extend(("spring_security_basics", "spring_boot_configuration"))
        elif language == "go":
            topics.append("go_error_handling")

        if not topics:
            category = CATEGORY_CODE_QUALITY
            topics.extend(GENERAL_FALLBACK_TOPICS)

        return category, tuple(topics)

    def _topics_from_error(
        self,
        error: dict,
        language: str,
        framework: str,
    ) -> tuple[str, tuple[str, ...]]:
        error_text = " ".join(
            str(error.get(key, ""))
            for key in ("type", "message", "file")
        ).lower()
        topics = []
        category = CATEGORY_CODE_QUALITY

        if self._has_any(error_text, ("modelnotfound", "not found", "no query results")):
            topics.append("missing_null_handling")
            if framework == "laravel":
                topics.append("laravel_exception_handling")

        if self._has_any(error_text, ("authorization", "permission", "forbidden", "403")):
            category = CATEGORY_SECURITY
            topics.append("laravel_authorization")

        if self._has_any(error_text, ("sql", "syntax error", "injection")):
            category = CATEGORY_SECURITY
            topics.append("owasp_sql_injection")

        if self._has_any(error_text, ("exception", "traceback", "stack trace")):
            topics.append("safe_error_reporting")

        if not topics:
            topics.extend(GENERAL_FALLBACK_TOPICS)

        if language == "php" and framework == "laravel" and "laravel_exception_handling" not in topics:
            topics.append("laravel_exception_handling")

        return category, tuple(topics)

    def _build_selection(
        self,
        language: str,
        framework: str,
        category: str,
        topics: tuple[str, ...],
        source: str,
    ) -> TopicSelection:
        normalized_topics = self._dedupe(topics)
        category = self._validate_category(category, normalized_topics)

        return TopicSelection(
            language=language,
            framework=framework,
            category=category,
            topics=normalized_topics,
            collections=self._collections_for(category, language),
            source=source,
        )

    def _validate_category(self, category: str, topics: tuple[str, ...]) -> str:
        if not topics:
            return CATEGORY_CODE_QUALITY

        parent_categories = [
            TOPIC_PARENT_CATEGORY.get(topic)
            for topic in topics
            if TOPIC_PARENT_CATEGORY.get(topic)
        ]
        if category in parent_categories:
            return category

        if parent_categories:
            return Counter(parent_categories).most_common(1)[0][0]

        return CATEGORY_CODE_QUALITY

    def _collections_for(self, category: str, language: str) -> tuple[str, ...]:
        suffix = LANGUAGE_COLLECTION_SUFFIX.get(language)

        if category == CATEGORY_SECURITY:
            collections = [f"security_{suffix}"] if suffix else []
            collections.append("security_general")
        elif category in (CATEGORY_BEST_PRACTICE, CATEGORY_FRAMEWORK):
            collections = [f"bestpractice_{suffix}"] if suffix else []
            collections.append("quality_general")
        elif category in (CATEGORY_CODE_QUALITY, CATEGORY_PERFORMANCE):
            collections = ["quality_general"]
            if suffix:
                collections.append(f"bestpractice_{suffix}")
        else:
            collections = []

        if not collections:
            collections = ["security_general", "quality_general"]

        return self._dedupe(tuple(collections))

    def _dedupe(self, values: tuple[str, ...]) -> tuple[str, ...]:
        result = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return tuple(result)

    def _has_any(self, text: str, needles: tuple[str, ...]) -> bool:
        """Return True when text contains any search string from needles."""
        return any(needle in text for needle in needles)
