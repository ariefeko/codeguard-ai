import os
import json
import httpx
from src.orchestration.prompts import build_code_review_prompt, build_bug_fix_prompt
from src.orchestration.tavily_client import CodeGuardSearch
from src.orchestration.schemas import validate_llm_output, BugAnalysis


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
    def __init__(self):
        self.search = CodeGuardSearch()

    def review_code(self, context: dict) -> str:
        """Entry point untuk GitHub webhook — code review."""
        search_results = self._enrich_with_search(context)
        prompt = build_code_review_prompt(context, search_results)
        return self._call_llm(prompt)

    def fix_bug(self, context: dict, error: dict) -> BugAnalysis | None:
        """
        Entry point untuk Sentry webhook — bug fix.
        Return None kalau semua provider gagal menghasilkan output yang
        valid sesuai BugAnalysis schema -> caller (worker.py) fallback
        ke manual GitHub Issue tanpa AI analysis.
        """
        search_results = self._search_for_error(error)
        prompt = build_bug_fix_prompt(context, error, search_results)
        return self._call_llm_structured(prompt)

    def _enrich_with_search(self, context: dict) -> dict:
        """Tavily search berdasarkan file extension."""
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

    def _search_for_error(self, error: dict) -> dict:
        """Tavily search untuk Sentry error context."""
        results = {}
        error_type = error.get("type", "")
        if error_type:
            results["error_info"] = self.search._search(
                f"{error_type} fix solution best practice"
            )
        return {k: v for k, v in results.items() if v}

    def _call_llm(self, prompt: str) -> str:
        """Kirim prompt ke provider dengan fallback chain. Dipakai review_code()."""
        for provider in PROVIDER_CHAIN:
            print(f"[Orchestrator] Trying: {provider['name']}")
            result = self._request(prompt, provider)
            if result:
                print(f"[Orchestrator] Success: {provider['name']}")
                return result
            print(f"[Orchestrator] Failed: {provider['name']}, trying next...")

        return "Error: all providers failed."

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

    def _request(self, prompt: str, provider: dict, json_mode: bool = False, max_tokens: int = MAX_TOKENS) -> str | None:
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
                timeout=60,
            )

            if response.status_code == 200:
                try:
                    text = response.text.strip()
                    if text.endswith("data: [DONE]"):
                        text = text[:-len("data: [DONE]")].strip()
                    data = json.loads(text)
                    return data["choices"][0]["message"]["content"]
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
