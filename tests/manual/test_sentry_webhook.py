"""
Local test for /webhook/sentry. It sends a synthetic Sentry Issue Alert
payload (data.event) with a valid HMAC signature to verify the complete flow
without triggering a real production error in Tagihin.

USAGE:
1. Start the local server in another terminal:
   uvicorn src.api.main:app --reload

2. Ensure .env contains the SENTRY_CLIENT_SECRET configured in the Sentry
   Internal Integration.

3. Run this script:
   export $(cat .env | grep -v '^#' | xargs)
   python test_sentry_webhook.py

4. Inspect webhook.py output in the uvicorn terminal for the "SENTRY WEBHOOK"
   and enqueue logs. Run an RQ worker in a third terminal and inspect worker.py
   output for the final result.
"""
import os
import json
import hashlib
import hmac
import requests

WEBHOOK_URL = "http://localhost:8000/webhook/sentry"

# Synthetic Issue Alert webhook payload (data.event) matching the Developer
# plan. The stack trace models a Laravel null pointer caused by an unloaded
# Eloquent relationship.
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
    """Match the verify_signature() logic in sentry_agent.py exactly."""
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def main():
    secret = os.getenv("SENTRY_CLIENT_SECRET")
    if not secret:
        print("❌ SENTRY_CLIENT_SECRET was not found in the environment.")
        print("   Run: export $(cat .env | grep -v '^#' | xargs)")
        return

    # IMPORTANT: serialize the body once and use the exact bytes for both the
    # signature and request. Different serialization would invalidate the
    # signature, which is why webhook.py reads raw_body before parsing.
    body_bytes = json.dumps(FAKE_PAYLOAD).encode("utf-8")
    signature = compute_signature(body_bytes, secret)

    headers = {
        "Content-Type": "application/json",
        "Sentry-Hook-Signature": signature,
        "Sentry-Hook-Resource": "issue",
    }

    print("=== Sending synthetic Sentry payload ===")
    print(f"URL: {WEBHOOK_URL}")
    print(f"Signature: {signature[:16]}...")
    print()

    try:
        response = requests.post(WEBHOOK_URL, data=body_bytes, headers=headers, timeout=10)
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to localhost:8000")
        print("   Ensure uvicorn is running: uvicorn src.api.main:app --reload")
        return

    print(f"Status code: {response.status_code}")
    try:
        print(f"Response: {json.dumps(response.json(), indent=2)}")
    except Exception:
        print(f"Response (raw): {response.text}")

    if response.status_code == 202:
        print("\n✅ Job enqueued successfully. Check the worker terminal for the result.")
    elif response.status_code == 401:
        print("\n❌ Signature rejected. Confirm that SENTRY_CLIENT_SECRET in .env")
        print("   exactly matches the client secret in the Sentry dashboard.")
    else:
        print("\n⚠️  Unexpected status; expected 202.")


def test_invalid_signature():
    """Verify that a request with an invalid signature is rejected."""
    body_bytes = json.dumps(FAKE_PAYLOAD).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Sentry-Hook-Signature": "intentionally-invalid-signature",
        "Sentry-Hook-Resource": "issue",
    }

    print("\n=== Testing invalid signature (expecting 401) ===")
    try:
        response = requests.post(WEBHOOK_URL, data=body_bytes, headers=headers, timeout=10)
        print(f"Status code: {response.status_code}")
        if response.status_code == 401:
            print("✅ Invalid signature correctly rejected with 401")
        else:
            print(f"❌ Expected 401 but received {response.status_code}")
            print("   verify_signature() is not working correctly.")
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to localhost:8000")


if __name__ == "__main__":
    main()
    test_invalid_signature()
