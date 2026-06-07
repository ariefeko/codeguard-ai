# src/orchestration/prompts.py

def build_code_review_prompt(context: dict) -> str:
    """
    Susun prompt code review dari context dict.
    context = {
        "changed_files": {"path": "content"},
        "related_files": {"path": "content"},
    }
    """
    lines = []

    lines.append("You are an expert code reviewer.")
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

    lines.append("\n=== INSTRUCTIONS ===")
    lines.append("- List issues found with severity: high / medium / low")
    lines.append("- For each issue: explain the problem and suggest a fix")
    lines.append("- If no issues found, say 'No issues found'")
    lines.append("- End with overall code quality score: 1-10")
    lines.append("- Be concise and specific")

    return "\n".join(lines)


def build_bug_fix_prompt(context: dict, error: dict) -> str:
    """
    Prompt untuk Sentry error — bug fix.
    Dipakai nanti saat Sentry webhook diintegrasikan.
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

    lines.append("\n=== INSTRUCTIONS ===")
    lines.append("- Identify the root cause of the error")
    lines.append("- Provide the fix as a code snippet")
    lines.append("- Explain why this fix works")

    return "\n".join(lines)

def add_line_numbers(content: str) -> str:
    """Tambahkan nomor baris ke content file."""
    lines = content.splitlines()
    numbered = []
    for i, line in enumerate(lines, start=1):
        numbered.append(f"{i:4d} | {line}")
    return "\n".join(numbered)