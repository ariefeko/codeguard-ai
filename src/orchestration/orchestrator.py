import os
import httpx
from src.orchestration.prompts import build_code_review_prompt, build_bug_fix_prompt
from src.orchestration.tavily_client import CodeGuardSearch


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Fallback chain — kalau model pertama gagal, coba berikutnya
MODEL_CHAIN = [
    "deepseek/deepseek-v4-flash",       # 3.81T, primary, big quota
    "google/gemini-3-flash-preview",     # 1.11T, cheap
    "meta-llama/llama-4-maverick:free",  # 163B, free
    "qwen/qwen3.6-plus",                 # 134B, $0.325/M — saving mode
    "qwen/qwen3.7-max",                  # 201B, $1.25/M — last resort
    "deepseek/deepseek-v4-pro",          # 1.74T, last fallback
]


class Orchestrator:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.search = CodeGuardSearch()

    def review_code(self, context: dict) -> str:
        """Entry point untuk GitHub webhook — code review."""
        # Tavily: cari info terbaru sebelum review
        search_results = self._enrich_with_search(context)

        prompt = build_code_review_prompt(context, search_results)
        return self._call_llm(prompt)

    def fix_bug(self, context: dict, error: dict) -> str:
        """Entry point untuk Sentry webhook — bug fix."""
        # Tavily: cari info terkait error
        search_results = self._search_for_error(error)

        prompt = build_bug_fix_prompt(context, error, search_results)
        return self._call_llm(prompt)

    def _enrich_with_search(self, context: dict) -> dict:
        """
        Jalankan Tavily search berdasarkan content dari changed files.
        Return dict berisi hasil search yang relevan.
        """
        results = {}

        # Deteksi bahasa/framework dari file extension
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

        # Selalu search OWASP top 10 terbaru
        results["owasp_top10"] = self.search.search_owasp("Top 10 2025")

        # Filter None values
        return {k: v for k, v in results.items() if v}

    def _search_for_error(self, error: dict) -> dict:
        """
        Tavily search untuk Sentry error context.
        """
        results = {}

        error_type = error.get("type", "")
        if error_type:
            results["error_info"] = self.search._search(
                f"{error_type} fix solution best practice"
            )

        return {k: v for k, v in results.items() if v}

    def _call_llm(self, prompt: str) -> str:
        """Kirim prompt ke OpenRouter dengan fallback chain."""
        for model in MODEL_CHAIN:
            print(f"[Orchestrator] Trying model: {model}")
            result = self._request(prompt, model)
            if result:
                print(f"[Orchestrator] Success with model: {model}")
                return result
            print(f"[Orchestrator] Failed with model: {model}, trying next...")

        return "Error: all models failed."

    def _request(self, prompt: str, model: str) -> str | None:
        """
        Satu request ke OpenRouter.
        Return None kalau gagal — trigger fallback.
        """
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        try:
            response = httpx.post(
                OPENROUTER_URL,
                headers=self.headers,
                json=payload,
                timeout=60,
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"[Orchestrator] HTTP {response.status_code}: {response.text[:200]}")
                return None

        except Exception as e:
            print(f"[Orchestrator] Exception: {e}")
            return None
