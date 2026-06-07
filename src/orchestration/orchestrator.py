import os
import httpx
from src.orchestration.prompts import build_code_review_prompt, build_bug_fix_prompt


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Fallback chain — kalau model pertama gagal, coba berikutnya
MODEL_CHAIN = [
    "deepseek/deepseek-chat-v3-0324:free",
    "google/gemini-flash-1.5:free",
    "meta-llama/llama-3.1-8b-instruct:free",
]


class Orchestrator:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def review_code(self, context: dict) -> str:
        """
        Entry point untuk GitHub webhook — code review.
        """
        prompt = build_code_review_prompt(context)
        return self._call_llm(prompt)

    def fix_bug(self, context: dict, error: dict) -> str:
        """
        Entry point untuk Sentry webhook — bug fix.
        """
        prompt = build_bug_fix_prompt(context, error)
        return self._call_llm(prompt)

    def _call_llm(self, prompt: str) -> str:
        """
        Kirim prompt ke OpenRouter dengan fallback chain.
        """
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