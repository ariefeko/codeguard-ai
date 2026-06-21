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

        STRUKTUR DIKONFIRMASI dari payload asli (20 Jun 2026, ModelNotFoundException
        dari Tagihin via Issue Alert webhook):
            data.issue.data.exception.values[].stacktrace.frames[]  -- detail lengkap
            data.issue.data.metadata                                -- ringkasan, sudah
                                                                        di-pre-filter Sentry
                                                                        ke frame in_app paling
                                                                        relevan
        metadata jadi sumber utama (lebih simpel, sudah dipilihkan Sentry sendiri),
        exception.values[].stacktrace.frames[] jadi fallback + sumber related_file_paths.
        """
        data = payload.get("data", {})

        # Jalur utama yang TERKONFIRMASI: data.issue.data.*
        issue = data.get("issue", {})
        issue_data = issue.get("data", issue) if isinstance(issue, dict) else {}

        if issue_data.get("metadata") or issue_data.get("exception"):
            return self._parse_from_issue_data(issue_data)

        # Jalur fallback 1: resource "error" langsung (Business/Enterprise, kemungkinan
        # struktur lebih flat, belum pernah dikonfirmasi langsung)
        if "error" in data:
            return self._parse_from_error_resource(data["error"])

        # Jalur fallback 2: data.event langsung (beberapa versi webhook alert rule)
        if "event" in data:
            return self._parse_from_event_resource(data["event"])

        # Jalur fallback 3: ada "issue" tapi tanpa metadata/exception (state change,
        # misal resolved/ignored -- tidak ada detail untuk dianalisis)
        if issue:
            print("[SentryAgent] Payload issue tanpa metadata/exception -- skip analysis")
            return None

        print(f"[SentryAgent] Struktur payload tidak dikenali. Keys: {list(data.keys())}")
        return None

    @staticmethod
    def _strip_leading_slash(filepath: str) -> str:
        """
        Sentry kasih path absolut container (/app/Livewire/Tagihan/Index.php),
        tapi GitHub API butuh path relatif ke root repo (app/Livewire/Tagihan/Index.php).
        Confirmed dari payload asli: semua filename punya leading slash.
        """
        return filepath.lstrip("/") if filepath else filepath

    def _parse_from_issue_data(self, issue_data: dict) -> dict | None:
        """
        Parse data.issue.data.* -- struktur TERKONFIRMASI dari payload asli.
        metadata sudah berisi filename/function yang sudah Sentry pre-filter ke
        frame in_app paling relevan -- jadi pakai ini sebagai sumber utama,
        bukan filter manual dari exception.values[].stacktrace.frames[].
        """
        metadata = issue_data.get("metadata", {})
        exception_values = issue_data.get("exception", {}).get("values", [])

        # related_file_paths tetap perlu digali dari stacktrace frames lengkap,
        # karena metadata cuma kasih SATU file (yang paling relevan), bukan semua
        # file in_app yang ikut terlibat di call stack.
        related_paths = []
        if exception_values:
            frames = exception_values[0].get("stacktrace", {}).get("frames", [])
            related_paths = list({
                self._strip_leading_slash(f.get("filename"))
                for f in frames
                if f.get("filename") and f.get("in_app")
            })

        if metadata.get("filename"):
            # Sumber utama: metadata, sudah dipilihkan Sentry
            file_path = self._strip_leading_slash(metadata.get("filename", ""))

            # metadata kadang punya field "lineno"/"line" langsung -- cek dulu
            # sebelum coba gali dari exception.values (yang bisa saja kosong,
            # terutama untuk payload jenis "issue" tanpa detail exception baru,
            # misal action=unresolved/escalated yang cuma state change).
            line = metadata.get("lineno") or metadata.get("line")
            if line is None and exception_values:
                line = self._extract_line_for_file(exception_values, metadata.get("filename", ""))

            if line is None:
                print(
                    f"[SentryAgent] Line number tidak ditemukan untuk {file_path} -- "
                    f"exception_values kosong: {not exception_values}, "
                    f"metadata keys: {list(metadata.keys())}"
                )

            return {
                "type": metadata.get("type", "Unknown"),
                "message": metadata.get("value", ""),
                "file": file_path,
                "line": line,
                "related_file_paths": related_paths or [file_path],
            }

        # Fallback: metadata tidak ada filename, gali manual dari exception frames
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

        print("[SentryAgent] issue_data tidak punya metadata.filename maupun exception.values")
        return None

    @staticmethod
    def _extract_line_for_file(exception_values: list, filename: str) -> int | None:
        """
        metadata tidak selalu menyertakan line number secara langsung -- gali
        dari frame yang filename-nya cocok dengan metadata.filename.
        """
        if not exception_values:
            return None
        frames = exception_values[0].get("stacktrace", {}).get("frames", [])
        for f in frames:
            if f.get("filename") == filename:
                return f.get("lineno")
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