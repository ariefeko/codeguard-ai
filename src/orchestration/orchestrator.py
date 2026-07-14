import logging
import os
from typing import Any

import httpx
from src.config import LLM_REQUEST_TIMEOUT_SECONDS
from src.orchestration.prompts import build_code_review_prompt, build_bug_fix_prompt
from src.orchestration.tavily_client import CodeGuardSearch
from src.rag import RAGPipeline, RAGSnippet
from src.orchestration.schemas import (
    BugAnalysis,
    extract_content,
    parse_llm_envelope,
    validate_llm_output,
)


logger = logging.getLogger(__name__)


# Provider endpoints
OPENAGENTIC_URL = "https://openagentic.id/api/v1/chat/completions"
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL      = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEEPSEEK_URL    = "https://api.deepseek.com/v1/chat/completions"

# Fallback chain using free-tier providers.
PROVIDER_CHAIN = [
    {
        "name"   : "DeepSeek V4 Flash (OpenAgentic)",
        "url"    : OPENAGENTIC_URL,
        "model"  : "deepseek-v4-flash",
        "api_key": "OPENAGENTIC_API_KEY",
    },
    {
        "name"   : "GLM-5 (OpenAgentic)",
        "url"    : OPENAGENTIC_URL,
        "model"  : "glm-5",
        "api_key": "OPENAGENTIC_API_KEY",
    },
    {
        "name"   : "Llama 3.3 70B (Groq)",
        "url"    : GROQ_URL,
        "model"  : "llama-3.3-70b-versatile",
        "api_key": "GROQ_API_KEY",
    },
]

MAX_TOKENS = 2048

# The Sentry path needs more room: DeepSeek includes reasoning_content before
# the final answer, and structured BugAnalysis JSON is more verbose than a
# free-form pull request comment.
MAX_TOKENS_STRUCTURED = MAX_TOKENS * 2


class Orchestrator:
    def __init__(self) -> None:
        self.search = CodeGuardSearch()
        self.rag = RAGPipeline()

    def review_code(self, context: dict[str, Any]) -> str | None:
        """Run a code review for the GitHub webhook path."""
        search_results = self._enrich_with_search(context)
        prompt = build_code_review_prompt(context, search_results)
        return self._call_llm(prompt)

    def fix_bug(
        self,
        context: dict[str, Any],
        error: dict[str, Any],
    ) -> BugAnalysis | None:
        """
        Run bug analysis for the Sentry webhook path.
        Return None when every provider fails to produce valid BugAnalysis
        output so the worker can create a manual GitHub issue without analysis.
        """
        search_results = self._search_for_error(error, context)
        prompt = build_bug_fix_prompt(context, error, search_results)
        return self._call_llm_structured(prompt)

    def _enrich_with_search(self, context: dict[str, Any]) -> dict[str, str]:
        """Curated RAG first, then Tavily fallback based on file extension."""
        rag_context = self._format_rag_for_context(context)
        if rag_context:
            return {"rag": rag_context}

        return self._search_review_references(context)

    def _search_review_references(self, context: dict[str, Any]) -> dict[str, str]:
        results = {}

        for file_path in context.get("changed_files", {}).keys():
            if file_path.endswith(".php"):
                results["php_security"] = self.search.search_best_practices(
                    "PHP Laravel", "security best practices"
                )
                results["owasp_injection"] = self.search.search_owasp("SQL injection")
                break
            elif file_path.endswith(".py"):
                results["python_security"] = self.search.search_best_practices(
                    "Python FastAPI", "security best practices"
                )
                break
            elif file_path.endswith((".js", ".ts")):
                results["js_security"] = self.search.search_best_practices(
                    "Node.js JavaScript", "security best practices"
                )
                break

        results["owasp_top10"] = self.search.search_owasp("Top 10 2025")

        return {k: v for k, v in results.items() if v}

    def _search_for_error(
        self,
        error: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Curated RAG first, then Tavily fallback for Sentry error context."""
        rag_context = self._format_rag_for_error(error, context or {})
        if rag_context:
            return {"rag": rag_context}

        results = {}
        error_type = error.get("type", "")
        if error_type:
            results["error_info"] = self.search._search(
                f"{error_type} fix solution best practice"
            )
        return {k: v for k, v in results.items() if v}

    def _format_rag_for_context(self, context: dict[str, Any]) -> str:
        try:
            snippets = self.rag.retrieve_for_context(context)
            return self._format_rag_snippets(snippets)
        except Exception as exc:
            logger.error(
                "RAG review enrichment failed",
                extra={"error_type": type(exc).__name__},
            )
            return ""

    def _format_rag_for_error(
        self,
        error: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        try:
            snippets = self.rag.retrieve_for_error(error, context)
            return self._format_rag_snippets(snippets)
        except Exception as exc:
            logger.error(
                "RAG error enrichment failed",
                extra={"error_type": type(exc).__name__},
            )
            return ""

    def _format_rag_snippets(self, snippets: list[RAGSnippet]) -> str:
        if not snippets:
            return ""
        return self.rag.format_prompt_snippets(snippets).strip()

    def _call_llm(self, prompt: str) -> str | None:
        """Send a prompt through the provider fallback chain for review_code()."""
        for provider in PROVIDER_CHAIN:
            logger.info("LLM provider attempt started")
            result = self._request(prompt, provider)
            if result:
                logger.info("LLM provider attempt succeeded")
                return result
            logger.warning("LLM provider attempt failed")

        return None

    def _call_llm_structured(self, prompt: str) -> BugAnalysis | None:
        """
        Send a prompt through the provider fallback chain and validate every
        response against the BugAnalysis schema before accepting it. This path
        is used only by fix_bug(); review_code() continues to use _call_llm().

        A schema validation failure is treated like a request failure and moves
        to the next provider in the chain.
        """
        for provider in PROVIDER_CHAIN:
            logger.info("Structured LLM provider attempt started")
            raw_content = self._request(prompt, provider, json_mode=True, max_tokens=MAX_TOKENS_STRUCTURED)

            if raw_content is None:
                logger.warning("Structured LLM provider request failed")
                continue

            result = validate_llm_output(raw_content)
            if result is not None:
                logger.info("Structured LLM provider attempt succeeded")
                return result

            logger.warning("Structured LLM response validation failed")

        logger.error("All structured LLM provider attempts failed")
        return None

    def _request(
        self,
        prompt: str,
        provider: dict[str, str],
        json_mode: bool = False,
        max_tokens: int = MAX_TOKENS,
    ) -> str | None:
        """
        Send one provider request and return None on failure.
        json_mode=True adds response_format: json_object for
        _call_llm_structured() only. The review_code path is unchanged because
        json_mode defaults to False and max_tokens defaults to MAX_TOKENS.
        """
        api_key = os.getenv(provider["api_key"])
        if not api_key:
            logger.warning("LLM provider API key is missing")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model"     : provider["model"],
            "max_tokens": max_tokens,
            "messages"  : [
                {"role": "user", "content": prompt}
            ],
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        # Provider URLs are hardcoded in PROVIDER_CHAIN and must remain trusted
        # configuration. Do not source them from webhook/user-controlled input.
        try:
            response = httpx.post(
                provider["url"],
                headers=headers,
                json=payload,
                timeout=LLM_REQUEST_TIMEOUT_SECONDS,
                verify=True,
            )

            if response.status_code == 200:
                try:
                    data = parse_llm_envelope(response.text)
                    if data is None:
                        raise ValueError("invalid response envelope")

                    content = extract_content(data)
                    if content is None:
                        raise ValueError("missing response content")

                    return content
                except Exception as exc:
                    response_size = len(response.text.encode("utf-8", errors="ignore"))
                    logger.warning(
                        "LLM provider response parsing failed",
                        extra={
                            "error_type": type(exc).__name__,
                            "status_code": response.status_code,
                            "response_size": response_size,
                        },
                    )
                    return None
            else:
                logger.warning(
                    "LLM provider returned an error response",
                    extra={"status_code": response.status_code},
                )
                return None

        except httpx.TransportError as exc:
            logger.warning(
                "LLM provider transport failure",
                extra={"error_type": type(exc).__name__},
            )
            return None
        except Exception as exc:
            logger.error(
                "Unexpected LLM provider failure",
                extra={"error_type": type(exc).__name__},
            )
            return None
