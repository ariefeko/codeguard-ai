# src/orchestration/prompts.py

def build_code_review_prompt(context: dict, search_results: dict = None) -> str:
    """
    Build a code review prompt from a context dictionary.
    context = {
        "changed_files": {"path": "content"},
        "related_files": {"path": "content"},
    }
    search_results = {
        "php_security": "...",
        "owasp_top10": "...",
        ...
    }
    """
    lines = []

    lines.append("You are an expert code reviewer with up-to-date knowledge of security advisories and best practices.")
    lines.append(
        "Review the following changed files and identify bugs, "
        "security issues, and code quality problems."
    )
    lines.append("")

    # Changed files
    lines.append("=== CHANGED FILES ===")
    for path, content in context["changed_files"].items():
        lines.append(f"\n[{path}]")
        lines.append(add_line_numbers(content))

    # Related files
    if context["related_files"]:
        lines.append("\n=== RELATED FILES (for context only, do not review) ===")
        for path, content in context["related_files"].items():
            lines.append(f"\n[{path}]")
            lines.append(add_line_numbers(content))

    rag_context, latest_references = split_rag_from_references(search_results)

    if rag_context:
        lines.append("\n=== CURATED RAG KNOWLEDGE ===")
        lines.append("Use these high-confidence curated snippets where relevant:")
        lines.append(rag_context)

    # Tavily search results
    if latest_references:
        lines.append("\n=== LATEST SECURITY & BEST PRACTICE REFERENCES ===")
        lines.append("Use the following up-to-date information to enrich your review:")
        for key, value in latest_references.items():
            label = key.replace("_", " ").upper()
            lines.append(f"\n[{label}]")
            lines.append(value)

    lines.append("\n=== INSTRUCTIONS ===")
    lines.append("- List issues found with severity: high / medium / low")
    lines.append("- For each issue: explain the problem and suggest a fix")
    lines.append("- Reference security advisories or best practices where relevant")
    lines.append("- If no issues found, say 'No issues found'")
    lines.append("- End with overall code quality score: 1-10")
    lines.append("- Be concise and specific")

    return "\n".join(lines)


def build_bug_fix_prompt(context: dict, error: dict, search_results: dict = None) -> str:
    """
    Build a bug-fix prompt for a Sentry error.
    """
    lines = []

    lines.append("You are an expert software debugger.")
    lines.append("Analyze the following error and suggest a fix.")
    lines.append("")

    lines.append("=== ERROR ===")
    lines.append(f"Type    : {error.get('type', 'Unknown')}")
    lines.append(f"Message : {error.get('message', '')}")
    lines.append(f"File    : {error.get('file', '')}")
    lines.append(f"Line    : {error.get('line', '')}")

    if context["changed_files"]:
        lines.append("\n=== AFFECTED FILES ===")
        for path, content in context["changed_files"].items():
            lines.append(f"\n[{path}]")
            lines.append(add_line_numbers(content))

    if context["related_files"]:
        lines.append("\n=== RELATED FILES (for context only) ===")
        for path, content in context["related_files"].items():
            lines.append(f"\n[{path}]")
            lines.append(add_line_numbers(content))

    rag_context, latest_references = split_rag_from_references(search_results)

    if rag_context:
        lines.append("\n=== CURATED RAG KNOWLEDGE ===")
        lines.append("Use these high-confidence curated snippets where relevant:")
        lines.append(rag_context)

    # Tavily search results
    if latest_references:
        lines.append("\n=== LATEST REFERENCES ===")
        lines.append("Use the following information to enrich your fix suggestion:")
        for key, value in latest_references.items():
            label = key.replace("_", " ").upper()
            lines.append(f"\n[{label}]")
            lines.append(value)

    lines.append("\n=== INSTRUCTIONS ===")
    lines.append("Respond with a single JSON object only -- no markdown fence, no commentary outside the JSON.")
    lines.append("The JSON object must have exactly these fields:")
    lines.append('- "status": one of "COMPLETE", "PARTIAL", or "INSUFFICIENT_DATA"')
    lines.append('- "root_cause": string, the underlying cause of the error, not just the symptom')
    lines.append('- "affected_file": string, the exact file path most likely responsible')
    lines.append('- "affected_line": integer or null, the specific line number if identifiable')
    lines.append('- "fix_steps": string, the reasoning and steps to resolve it properly')
    lines.append('- "quick_fix_code": string, the fix as a code snippet')
    lines.append('- "prevention": string, how to prevent this class of error from recurring')
    lines.append('- "inferences": array of objects, each with "claim" (string), "confidence" ("high"/"medium"/"low"), and "basis" (string citing the specific line or field that supports the claim)')
    lines.append('- "insufficient_data_reason": string or null, required if status is "INSUFFICIENT_DATA"')
    lines.append("State explicitly when a claim is an inference rather than a directly observed fact, and cite its basis.")
    lines.append("If the evidence is insufficient to identify the root cause with at least medium confidence, set status to INSUFFICIENT_DATA and explain what additional information would help.")
    lines.append("Reference any relevant security advisories in fix_steps or prevention if applicable.")

    return "\n".join(lines)


def add_line_numbers(content: str) -> str:
    """Add line numbers to file contents."""
    lines = content.splitlines()
    numbered = []
    for i, line in enumerate(lines, start=1):
        numbered.append(f"{i:4d} | {line}")
    return "\n".join(numbered)


def split_rag_from_references(search_results: dict | None) -> tuple[str, dict]:
    if not search_results:
        return "", {}

    rag_context = str(search_results.get("rag") or "").strip()
    latest_references = {
        key: value
        for key, value in search_results.items()
        if key != "rag" and value
    }
    return rag_context, latest_references
