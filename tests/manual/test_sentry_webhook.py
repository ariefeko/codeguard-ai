"""
Test lokal untuk endpoint /webhook/sentry -- kirim payload Sentry palsu
(struktur Issue Alert webhook, data.event) dengan signature HMAC yang
benar, supaya bisa verifikasi seluruh flow tanpa perlu trigger error
production sungguhan di Tagihin.

CARA PAKAI:
1. Jalankan server lokal dulu di terminal lain:
   uvicorn src.api.main:app --reload

2. Pastikan .env sudah ada SENTRY_CLIENT_SECRET (yang sama dengan yang
   di-set di Internal Integration Sentry).

3. Jalankan script ini:
   export $(cat .env | grep -v '^#' | xargs)
   python test_sentry_webhook.py

4. Perhatikan output webhook.py di terminal uvicorn -- harus muncul
   log "SENTRY WEBHOOK", lalu job enqueued. Cek juga worker.py log
   (jalankan rq worker di terminal ketiga) untuk lihat hasil akhir.
"""
import os
import json
import hashlib
import hmac
import requests

WEBHOOK_URL = "http://localhost:8000/webhook/sentry"

# Payload dummy -- struktur Issue Alert webhook (data.event), sesuai
# yang dipakai akun Developer plan (gratis). Stack trace contoh kasus
# Laravel: null pointer karena relasi Eloquent tidak di-load.
FAKE_PAYLOAD = {
    "action": "triggered",
    "installation": {
        "uuid": "fake-installation-uuid-1234",
    },
    "data": {
        "event": {
            "event_id": "fake1234567890abcdef1234567890ab",
            "platform": "php",
            "title": "Error: Call to a member function format() on null",
            "exception": {
                "values": [
                    {
                        "type": "Error",
                        "value": "Call to a member function format() on null",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "vendor/laravel/framework/src/Illuminate/Routing/Controller.php",
                                    "lineno": 54,
                                    "in_app": False,
                                },
                                {
                                    "filename": "app/Http/Controllers/InvoiceController.php",
                                    "lineno": 87,
                                    "in_app": True,
                                },
                                {
                                    "filename": "app/Models/Invoice.php",
                                    "lineno": 42,
                                    "in_app": True,
                                },
                            ]
                        },
                    }
                ]
            },
        }
    },
}


def compute_signature(body_bytes: bytes, secret: str) -> str:
    """Sama persis dengan logic verify_signature() di sentry_agent.py."""
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def main():
    secret = os.getenv("SENTRY_CLIENT_SECRET")
    if not secret:
        print("❌ SENTRY_CLIENT_SECRET tidak ditemukan di environment.")
        print("   Jalankan: export $(cat .env | grep -v '^#' | xargs)")
        return

    # PENTING: body harus di-serialize SEKALI, dipakai utuh untuk
    # signature DAN untuk request -- kalau beda serialisasi, signature
    # tidak akan cocok (sama seperti alasan kita ambil raw_body di
    # webhook.py sebelum parsing).
    body_bytes = json.dumps(FAKE_PAYLOAD).encode("utf-8")
    signature = compute_signature(body_bytes, secret)

    headers = {
        "Content-Type": "application/json",
        "Sentry-Hook-Signature": signature,
        "Sentry-Hook-Resource": "issue",
    }

    print("=== Mengirim payload Sentry palsu ===")
    print(f"URL: {WEBHOOK_URL}")
    print(f"Signature: {signature[:16]}...")
    print()

    try:
        response = requests.post(WEBHOOK_URL, data=body_bytes, headers=headers, timeout=10)
    except requests.exceptions.ConnectionError:
        print("❌ Tidak bisa connect ke localhost:8000")
        print("   Pastikan uvicorn sudah jalan: uvicorn src.api.main:app --reload")
        return

    print(f"Status code: {response.status_code}")
    try:
        print(f"Response: {json.dumps(response.json(), indent=2)}")
    except Exception:
        print(f"Response (raw): {response.text}")

    if response.status_code == 202:
        print("\n✅ Job berhasil di-enqueue. Cek terminal worker untuk hasil akhir.")
    elif response.status_code == 401:
        print("\n❌ Signature ditolak. Cek apakah SENTRY_CLIENT_SECRET di .env")
        print("   sama persis dengan Client Secret di dashboard Sentry.")
    else:
        print(f"\n⚠️  Status tidak sesuai ekspektasi (harusnya 202).")


def test_invalid_signature():
    """Test tambahan: pastikan request dengan signature SALAH benar-benar ditolak."""
    body_bytes = json.dumps(FAKE_PAYLOAD).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Sentry-Hook-Signature": "signature-yang-sengaja-salah",
        "Sentry-Hook-Resource": "issue",
    }

    print("\n=== Test signature SALAH (harus ditolak 401) ===")
    try:
        response = requests.post(WEBHOOK_URL, data=body_bytes, headers=headers, timeout=10)
        print(f"Status code: {response.status_code}")
        if response.status_code == 401:
            print("✅ Benar -- signature salah ditolak dengan 401")
        else:
            print(f"❌ SALAH -- harusnya 401, tapi dapat {response.status_code}")
            print("   Ini berarti verify_signature() TIDAK bekerja dengan benar!")
    except requests.exceptions.ConnectionError:
        print("❌ Tidak bisa connect ke localhost:8000")


if __name__ == "__main__":
    main()
    test_invalid_signature()