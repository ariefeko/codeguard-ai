"""
src/agents/sentry_agent.py

Parse Sentry webhook payloads, extract essential error data (type, message,
file, and line), and verify that requests genuinely originate from Sentry.

IMPORTANT:
Sentry has two webhook delivery paths with different payload structures:
1. The "error" resource (data.error.*) is available only on Business and
   Enterprise plans, not the Developer plan currently used by this project.
2. Issue Alert webhooks (data.event.*) are available on every plan, including
   Developer, and are the practical integration path currently in use.

The parser supports both paths. It tries data.error first and falls back to
data.event so it continues to work after a plan upgrade or a change in
Sentry's default delivery path.

Referensi: https://docs.sentry.io/organization/integrations/integration-platform/webhooks/
"""
import hashlib
import hmac
import os


class SentryAgent:
    """Parse Sentry webhook payloads; this is not a filesystem scanner."""

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """
        Verify the Sentry-Hook-Signature header.
        Compute HMAC SHA-256 over the raw body using the client secret as key.
        Return False when the secret is missing or the signature does not match;
        the webhook caller must reject the request in either case.
        """
        secret = os.getenv("SENTRY_CLIENT_SECRET")
        if not secret:
            print("[SentryAgent] SENTRY_CLIENT_SECRET is not configured — rejecting request")
            return False

        computed = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest().encode("ascii")

        provided = (signature_header or "").encode("utf-8")
        normalized = provided.ljust(len(computed), b"\x00")[: len(computed)]
        matches = hmac.compare_digest(computed, normalized)
        has_expected_length = len(provided) == len(computed)

        if not signature_header:
            print("[SentryAgent] Sentry-Hook-Signature header is missing")
        elif not has_expected_length or not matches:
            print("[SentryAgent] Sentry-Hook-Signature is invalid")

        return bool(signature_header) and has_expected_length and matches

    def parse_error(self, payload: dict) -> dict | None:
        """
        Extract error context from a Sentry payload.
        Return a dictionary containing type, message, file, line, and
        related_file_paths. Return None when the payload has no recognized
        error structure, such as installation or comment payloads.

        Confirmed structure from a real Tagihin ModelNotFoundException payload
        received through an Issue Alert webhook on June 20, 2026:
            data.issue.data.exception.values[].stacktrace.frames[]  -- full details
            data.issue.data.metadata                                -- Sentry-filtered
                                                                       summary for the
                                                                       most relevant
                                                                       in_app frame
        Metadata is the primary source because Sentry already selects the most
        relevant frame. Stack trace frames provide the fallback and related paths.
        """
        data = payload.get("data", {})

        # Use the Sentry issue_id as the Redis deduplication key.
        # For resource=issue it is in data.issue.id.
        # For resource=event_alert it is in data.issue_id or data.issue.id.
        issue = data.get("issue", {})
        issue_id = (
            str(issue.get("id", ""))
            or str(data.get("issue_id", ""))
            or str(data.get("event", {}).get("issue_id", ""))
            or ""
        )

        # Confirmed primary path: data.issue.data.*
        issue_data = issue.get("data", issue) if isinstance(issue, dict) else {}

        if issue_data.get("metadata") or issue_data.get("exception"):
            result = self._parse_from_issue_data(issue_data)
            if result:
                result["issue_id"] = issue_id
            return result

        # Fallback 1: direct "error" resource (Business/Enterprise)
        if "error" in data:
            result = self._parse_from_error_resource(data["error"])
            if result:
                result["issue_id"] = issue_id
            return result

        # Fallback 2: direct data.event
        if "event" in data:
            result = self._parse_from_event_resource(data["event"])
            if result:
                result["issue_id"] = issue_id
            return result

        # Fallback 3: state change without exception details
        if issue:
            print("[SentryAgent] Issue payload has no metadata or exception — skipping analysis")
            return None

        print(f"[SentryAgent] Unrecognized payload structure. Keys: {list(data.keys())}")
        return None

    @staticmethod
    def _strip_leading_slash(filepath: str) -> str:
        """
        Sentry provides absolute container paths (/app/Livewire/Tagihan/Index.php),
        while the GitHub API requires repository-relative paths
        (app/Livewire/Tagihan/Index.php). Real payloads consistently include a
        leading slash.
        """
        return filepath.lstrip("/") if filepath else filepath

    def _parse_from_issue_data(self, issue_data: dict) -> dict | None:
        """
        Parse data.issue.data.* using the structure confirmed from real payloads.
        Metadata contains the filename and function prefiltered by Sentry to the
        most relevant in_app frame, so it is the primary source instead of a
        manual scan of exception.values[].stacktrace.frames[].
        """
        metadata = issue_data.get("metadata", {})
        exception_values = issue_data.get("exception", {}).get("values", [])

        # Collect related paths from all stack trace frames because metadata
        # provides only the single most relevant file, not every in_app file.
        related_paths = []
        if exception_values:
            frames = exception_values[0].get("stacktrace", {}).get("frames", [])
            related_paths = list({
                self._strip_leading_slash(f.get("filename"))
                for f in frames
                if f.get("filename") and f.get("in_app")
            })

        if metadata.get("filename"):
            # Primary source: metadata selected by Sentry.
            file_path = self._strip_leading_slash(metadata.get("filename", ""))

            # Metadata may contain a direct "lineno" or "line" field. Check it
            # before scanning exception values, which may be empty for issue
            # state changes such as unresolved or escalated actions.
            line = metadata.get("lineno") or metadata.get("line")
            if line is None and exception_values:
                line = self._extract_line_for_file(exception_values, metadata.get("filename", ""))

            if line is None:
                print(
                    f"[SentryAgent] Line number was not found for {file_path} — "
                    f"exception_values empty: {not exception_values}, "
                    f"metadata keys: {list(metadata.keys())}"
                )

            return {
                "type": metadata.get("type", "Unknown"),
                "message": metadata.get("value", ""),
                "file": file_path,
                "line": line,
                "related_file_paths": related_paths or [file_path],
            }

        # Fallback: scan exception frames when metadata has no filename.
        if exception_values:
            primary = exception_values[0]
            frames = primary.get("stacktrace", {}).get("frames", [])
            in_app_frames = [f for f in frames if f.get("in_app")]
            target_frame = in_app_frames[-1] if in_app_frames else (frames[-1] if frames else {})

            return {
                "type": primary.get("type", "Unknown"),
                "message": primary.get("value", ""),
                "file": self._strip_leading_slash(target_frame.get("filename", "")),
                "line": target_frame.get("lineno"),
                "related_file_paths": related_paths,
            }

        print("[SentryAgent] issue_data has neither metadata.filename nor exception.values")
        return None

    @staticmethod
    def _extract_line_for_file(exception_values: list, filename: str) -> int | None:
        """
        Metadata does not always include a line number directly, so find it in
        the frame whose filename matches metadata.filename.
        """
        if not exception_values:
            return None
        frames = exception_values[0].get("stacktrace", {}).get("frames", [])
        for f in frames:
            if f.get("filename") == filename:
                return f.get("lineno")
        return None

    def _parse_from_error_resource(self, error: dict) -> dict | None:
        """Parse data.error.* using the Business/Enterprise error resource structure."""
        exception_values = error.get("exception", {}).get("values", [])
        if not exception_values:
            print("[SentryAgent] data.error has no exception.values")
            return None

        primary = exception_values[0]
        frames = primary.get("stacktrace", {}).get("frames", [])

        # The most relevant frame is usually the final in_app frame closest to
        # the crash point, representing project rather than dependency code.
        in_app_frames = [f for f in frames if f.get("in_app")]
        target_frame = in_app_frames[-1] if in_app_frames else (frames[-1] if frames else {})

        related_paths = list({
            self._strip_leading_slash(f.get("filename")) for f in frames
            if f.get("filename") and f.get("in_app")
        })

        return {
            "type": primary.get("type", error.get("metadata", {}).get("type", "Unknown")),
            "message": primary.get("value", error.get("message", "")),
            "file": self._strip_leading_slash(target_frame.get("filename", "")),
            "line": target_frame.get("lineno"),
            "related_file_paths": related_paths,
        }

    def _parse_from_event_resource(self, event: dict) -> dict | None:
        """
        Parse data.event.* using the Developer-plan Issue Alert structure.
        Exact fields vary by platform, so use layered fallbacks rather than
        assuming one fixed shape.
        """
        exception_values = event.get("exception", {}).get("values", [])

        if exception_values:
            primary = exception_values[0]
            frames = primary.get("stacktrace", {}).get("frames", [])
            in_app_frames = [f for f in frames if f.get("in_app")]
            target_frame = in_app_frames[-1] if in_app_frames else (frames[-1] if frames else {})
            related_paths = list({
                self._strip_leading_slash(f.get("filename"))
                for f in frames
                if f.get("filename") and f.get("in_app")
            })

            return {
                "type": primary.get("type", "Unknown"),
                "message": primary.get("value", event.get("message", "")),
                "file": self._strip_leading_slash(target_frame.get("filename", "")),
                "line": target_frame.get("lineno"),
                "related_file_paths": related_paths,
            }

        # Minimal fallback for the rare event that has no exception or stack
        # trace details: preserve the available title without crashing.
        title = event.get("title", "")
        if not title:
            print("[SentryAgent] data.event has neither an exception nor a title")
            return None

        print("[SentryAgent] data.event has no stack trace details — using the title only")
        return {
            "type": event.get("metadata", {}).get("type", "Unknown"),
            "message": title,
            "file": "",
            "line": None,
            "related_file_paths": [],
        }
