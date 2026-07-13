import os
from tavily import TavilyClient


class CodeGuardSearch:
    def __init__(self):
        self.client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    def search_security_advisories(self, packages: list[str]) -> list[dict]:
        """
        Find security advisories for packages discovered in code.
        Example: ["laravel/framework 10.x", "symfony/http-foundation 6.4"]
        """
        results = []
        for package in packages:
            query = f"{package} security vulnerability CVE advisory 2024 2025"
            result = self._search(query)
            if result:
                results.append({
                    "package": package,
                    "findings": result
                })
        return results

    def search_best_practices(self, language: str, context: str) -> str | None:
        """
        Find current best practices for a language or framework.
        Example: language="PHP Laravel", context="authentication"
        """
        query = f"{language} {context} best practices 2025"
        return self._search(query)

    def search_owasp(self, issue_type: str) -> str | None:
        """
        Find OWASP recommendations for a specific issue type.
        Example: issue_type="SQL injection", "XSS", "CSRF"
        """
        query = f"OWASP {issue_type} prevention best practice 2025"
        return self._search(query)

    def search_cve(self, package: str, version: str) -> str | None:
        """
        Find CVEs for a specific package and version.
        """
        query = f"CVE {package} {version} vulnerability security"
        return self._search(query)

    def _search(self, query: str) -> str | None:
        """
        Core search method.
        Return a summary string ready to include in a prompt.
        """
        try:
            print(f"[Tavily] Searching: {query}")
            response = self.client.search(
                query=query,
                search_depth="basic",
                max_results=3,
                include_answer=True,
            )

            # Prefer the answer summary when available.
            answer = response.get("answer")
            if answer:
                return answer

            # Fall back to snippets from search results.
            results = response.get("results", [])
            if results:
                snippets = [r.get("content", "") for r in results[:2]]
                return " ".join(snippets)[:500]

            return None

        except Exception as e:
            print(f"[Tavily] Error: {e}")
            return None
