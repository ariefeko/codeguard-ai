import os
import httpx
from src.orchestration.prompts import build_code_review_prompt, build_bug_fix_prompt


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

    def review_code(self, context: dict) -> str:
        """ Entry point untuk GitHub webhook — code review. """
        prompt = build_code_review_prompt(context)
        return self._call_llm(prompt)

    def fix_bug(self, context: dict, error: dict) -> str:
        """ Entry point untuk Sentry webhook — bug fix. """
        prompt = build_bug_fix_prompt(context, error)
        return self._call_llm(prompt)

    def _call_llm(self, prompt: str) -> str:
        """ Kirim prompt ke OpenRouter dengan fallback chain. """
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
                headers = self.headers,
                json = payload,
                timeout = 60,
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