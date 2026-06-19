"""
src/agents/sentry_agent.py

Parser untuk payload webhook Sentry. Extract data penting (error type,
message, file, line) dari struktur payload Sentry, dan verifikasi
signature supaya request benar-benar dari Sentry.

CATATAN PENTING:
Sentry punya dua jalur pengiriman webhook dengan struktur payload BEDA:
1. Resource "error" (data.error.*) -- hanya tersedia di plan Business/
   Enterprise. TIDAK tersedia di Developer plan (gratis) yang dipakai
   project ini.
2. Issue Alert webhook (data.event.*) -- tersedia di semua plan
   termasuk Developer (gratis). Ini yang realistis dipakai sekarang.

Parser ini robust terhadap KEDUANYA -- coba data.error dulu, fallback
ke data.event -- supaya tetap jalan kalau suatu saat plan di-upgrade
atau Sentry mengubah jalur pengiriman default.

Referensi: https://docs.sentry.io/organization/integrations/integration-platform/webhooks/
"""
import hashlib
import hmac
import os


class SentryAgent:
    """Parser payload webhook Sentry -- bukan filesystem scanner."""

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """
        Verifikasi Sentry-Hook-Signature header.
        HMAC SHA-256 dari raw request body, pakai Client Secret sebagai key.
        Return False kalau secret tidak diset atau signature tidak cocok --
        caller (webhook.py) WAJIB reject request kalau ini False.
        """
        secret = os.getenv("SENTRY_CLIENT_SECRET")
        if not secret:
            print("[SentryAgent] SENTRY_CLIENT_SECRET tidak diset -- menolak request")
            return False

        if not signature_header:
            print("[SentryAgent] Tidak ada Sentry-Hook-Signature header")
            return False

        computed = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, signature_header)

    def parse_error(self, payload: dict) -> dict | None:
        """
        Extract error context dari payload Sentry.
        Return dict dengan keys: type, message, file, line, related_file_paths.
        Return None kalau payload tidak punya struktur error yang dikenali
        (misal payload installation/comment, bukan issue/error/alert).
        """
        data = payload.get("data", {})

        # Jalur 1: resource "error" (Business/Enterprise plan)
        if "error" in data:
            return self._parse_from_error_resource(data["error"])

        # Jalur 2: Issue Alert webhook (Developer plan, gratis)
        if "event" in data:
            return self._parse_from_event_resource(data["event"])

        # Jalur 3: resource "issue" tanpa nested event (issue state change,
        # misal resolved/ignored -- biasanya tidak ada exception detail)
        if "issue" in data:
            print("[SentryAgent] Payload issue tanpa detail exception -- skip analysis")
            return None

        print(f"[SentryAgent] Struktur payload tidak dikenali. Keys: {list(data.keys())}")
        return None

    def _parse_from_error_resource(self, error: dict) -> dict | None:
        """Parse data.error.* -- struktur resource 'error' (Business/Enterprise)."""
        exception_values = error.get("exception", {}).get("values", [])
        if not exception_values:
            print("[SentryAgent] data.error tidak punya exception.values")
            return None

        primary = exception_values[0]
        frames = primary.get("stacktrace", {}).get("frames", [])

        # Frame paling relevan biasanya yang terakhir (paling dekat ke titik crash)
        # dan in_app=True (kode milik project, bukan dependency/library)
        in_app_frames = [f for f in frames if f.get("in_app")]
        target_frame = in_app_frames[-1] if in_app_frames else (frames[-1] if frames else {})

        related_paths = list({
            f.get("filename") for f in frames
            if f.get("filename") and f.get("in_app")
        })

        return {
            "type": primary.get("type", error.get("metadata", {}).get("type", "Unknown")),
            "message": primary.get("value", error.get("message", "")),
            "file": target_frame.get("filename", ""),
            "line": target_frame.get("lineno"),
            "related_file_paths": related_paths,
        }

    def _parse_from_event_resource(self, event: dict) -> dict | None:
        """
        Parse data.event.* -- struktur Issue Alert webhook (Developer plan).
        Field exact bisa bervariasi tergantung platform (JS/Python/PHP dll)
        -- ambil dengan fallback berlapis, jangan asumsikan satu bentuk pasti.
        """
        exception_values = event.get("exception", {}).get("values", [])

        if exception_values:
            primary = exception_values[0]
            frames = primary.get("stacktrace", {}).get("frames", [])
            in_app_frames = [f for f in frames if f.get("in_app")]
            target_frame = in_app_frames[-1] if in_app_frames else (frames[-1] if frames else {})
            related_paths = list({
                f.get("filename") for f in frames
                if f.get("filename") and f.get("in_app")
            })

            return {
                "type": primary.get("type", "Unknown"),
                "message": primary.get("value", event.get("message", "")),
                "file": target_frame.get("filename", ""),
                "line": target_frame.get("lineno"),
                "related_file_paths": related_paths,
            }

        # Fallback minimal -- event ada tapi tidak ada exception/stacktrace
        # detail (jarang, tapi jangan crash, kasih apa yang ada)
        title = event.get("title", "")
        if not title:
            print("[SentryAgent] data.event tidak punya exception maupun title")
            return None

        print("[SentryAgent] data.event tidak punya stacktrace detail -- fallback ke title saja")
        return {
            "type": event.get("metadata", {}).get("type", "Unknown"),
            "message": title,
            "file": "",
            "line": None,
            "related_file_paths": [],
        }