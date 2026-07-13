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


# Provider endpoints
OPENAGENTIC_URL = "https://openagentic.id/api/v1/chat/completions"
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL      = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEEPSEEK_URL    = "https://api.deepseek.com/v1/chat/completions"

# Fallback chain — semua free tier
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

# Path Sentry (fix_bug) butuh lebih banyak ruang: DeepSeek menyertakan
# reasoning_content sebelum jawaban final, dan output JSON terstruktur
# (BugAnalysis) lebih verbose dari teks bebas komentar PR.
MAX_TOKENS_STRUCTURED = MAX_TOKENS * 2


class Orchestrator:
    def __init__(self) -> None:
        self.search = CodeGuardSearch()
        self.rag = RAGPipeline()

    def review_code(self, context: dict[str, Any]) -> str | None:
        """Entry point untuk GitHub webhook — code review."""
        search_results = self._enrich_with_search(context)
        prompt = build_code_review_prompt(context, search_results)
        return self._call_llm(prompt)

    def fix_bug(
        self,
        context: dict[str, Any],
        error: dict[str, Any],
    ) -> BugAnalysis | None:
        """
        Entry point untuk Sentry webhook — bug fix.
        Return None kalau semua provider gagal menghasilkan output yang
        valid sesuai BugAnalysis schema -> caller (worker.py) fallback
        ke manual GitHub Issue tanpa AI analysis.
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
            print(f"[RAG] Review enrichment failed: {type(exc).__name__}")
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
            print(f"[RAG] Error enrichment failed: {type(exc).__name__}")
            return ""

    def _format_rag_snippets(self, snippets: list[RAGSnippet]) -> str:
        if not snippets:
            return ""
        return self.rag.format_prompt_snippets(snippets).strip()

    def _call_llm(self, prompt: str) -> str | None:
        """Kirim prompt ke provider dengan fallback chain. Dipakai review_code()."""
        for provider in PROVIDER_CHAIN:
            print(f"[Orchestrator] Trying: {provider['name']}")
            result = self._request(prompt, provider)
            if result:
                print(f"[Orchestrator] Success: {provider['name']}")
                return result
            print(f"[Orchestrator] Failed: {provider['name']}, trying next...")

        return None

    def _call_llm_structured(self, prompt: str) -> BugAnalysis | None:
        """
        Kirim prompt ke provider dengan fallback chain, DAN validasi tiap
        jawaban terhadap BugAnalysis schema sebelum diterima. Dipakai
        fix_bug() saja -- review_code() tetap pakai _call_llm() di atas,
        tidak tersentuh.

        Kalau provider menjawab tapi gagal validasi schema (bukan error
        HTTP/koneksi), itu tetap dianggap gagal -> lanjut ke provider
        berikutnya di chain, sama seperti gagal request biasa.
        """
        for provider in PROVIDER_CHAIN:
            print(f"[Orchestrator] Trying (structured): {provider['name']}")
            raw_content = self._request(prompt, provider, json_mode=True, max_tokens=MAX_TOKENS_STRUCTURED)

            if raw_content is None:
                print(f"[Orchestrator] Failed (request): {provider['name']}, trying next...")
                continue

            result = validate_llm_output(raw_content)
            if result is not None:
                print(f"[Orchestrator] Success (structured): {provider['name']}")
                return result

            print(f"[Orchestrator] Failed (schema validation): {provider['name']}, trying next...")

        print("[Orchestrator] All providers failed structured validation.")
        return None

    def _request(
        self,
        prompt: str,
        provider: dict[str, str],
        json_mode: bool = False,
        max_tokens: int = MAX_TOKENS,
    ) -> str | None:
        """
        Satu request ke provider. Return None kalau gagal.
        json_mode=True menambahkan response_format: json_object -- dipakai
        _call_llm_structured() saja. _call_llm() (review_code) tidak
        terpengaruh karena defaultnya False dan max_tokens default MAX_TOKENS.
        """
        api_key = os.getenv(provider["api_key"])
        if not api_key:
            print(f"[Orchestrator] Missing API key: {provider['api_key']}")
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
                except Exception as e:
                    response_size = len(response.text.encode("utf-8", errors="ignore"))
                    print(
                        "[Orchestrator] Provider response parse failed: "
                        f"{type(e).__name__}; status={response.status_code}; "
                        f"bytes={response_size}"
                    )
                    return None
            else:
                print(f"[Orchestrator] HTTP {response.status_code} from provider")
                return None

        except Exception as e:
            print(f"[Orchestrator] Exception: {e}")
            return None
