import base64
import json
import unittest

from insightai.auth.crypto import decrypt_blob, encrypt_blob, generate_data_key, load_data_key
from insightai.auth.redact import redact_text
from insightai.auth.storage_state import has_session_data, normalize_origin, scope_storage_state
from insightai.auth.session_capture import default_profile_id, resume_confirmed
from insightai.auth.store import validate_database_url, validate_profile_id, AuthStoreError


class CryptoTests(unittest.TestCase):
    def test_round_trip(self):
        key = base64.b64decode(generate_data_key())
        ciphertext, nonce = encrypt_blob(b'{"cookies":[]}', key)
        self.assertEqual(decrypt_blob(ciphertext, nonce, key), b'{"cookies":[]}')

    def test_wrong_key_fails(self):
        key = base64.b64decode(generate_data_key())
        other = base64.b64decode(generate_data_key())
        ciphertext, nonce = encrypt_blob(b"secret", key)
        with self.assertRaises(Exception):
            decrypt_blob(ciphertext, nonce, other)

    def test_aad_binds_ciphertext(self):
        key = base64.b64decode(generate_data_key())
        ciphertext, nonce = encrypt_blob(b"cookies", key, aad=b"profile\nhttp://localhost")
        with self.assertRaises(Exception):
            decrypt_blob(ciphertext, nonce, key, aad=b"other\nhttp://localhost")

    def test_load_key_from_env(self):
        import os

        os.environ["AUTH_DATA_KEY"] = generate_data_key()
        try:
            self.assertEqual(len(load_data_key()), 32)
        finally:
            del os.environ["AUTH_DATA_KEY"]


class StorageStateTests(unittest.TestCase):
    def test_scope_drops_other_sites(self):
        raw = {
            "cookies": [
                {"name": "sid", "value": "abc", "domain": "localhost"},
                {"name": "other", "value": "nope", "domain": "evil.test"},
            ],
            "origins": [
                {
                    "origin": "http://localhost:5500",
                    "localStorage": [{"name": "cart", "value": "[]"}],
                },
                {
                    "origin": "https://evil.test",
                    "localStorage": [{"name": "token", "value": "secret"}],
                },
            ],
        }
        scoped = scope_storage_state(raw, "http://localhost:5500/login.html")
        self.assertEqual([cookie["name"] for cookie in scoped["cookies"]], ["sid"])
        self.assertEqual(scoped["origins"][0]["origin"], "http://localhost:5500")
        self.assertTrue(has_session_data(scoped))
        blob = json.dumps(scoped)
        self.assertNotIn("evil.test", blob)
        self.assertNotIn("secret", blob)

    def test_parent_domain_cookie_kept(self):
        scoped = scope_storage_state(
            {"cookies": [{"name": "sid", "value": "abc", "domain": ".example.com"}], "origins": []},
            "https://www.example.com",
        )
        self.assertEqual(scoped["cookies"][0]["name"], "sid")

    def test_origin_normalizes_default_port(self):
        self.assertEqual(normalize_origin("https://Example.com:443/path"), "https://example.com")


class RedactTests(unittest.TestCase):
    def test_headers(self):
        text = "cookie: sid=abc\nAuthorization: Bearer tok\nset-cookie: sid=abc; HttpOnly"
        cleaned = redact_text(text)
        self.assertNotIn("abc", cleaned)
        self.assertNotIn("tok", cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_jwt(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturevalue"
        self.assertNotIn("eyJzdWIi", redact_text(f"token {token}"))

    def test_api_key_query_and_json(self):
        text = 'x-api-key: supersecret\nhttps://app.test/cb?token=abc123\n{"password":"secret"}'
        cleaned = redact_text(text)
        self.assertNotIn("supersecret", cleaned)
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("secret", cleaned)


class ResumeTests(unittest.TestCase):
    def test_continue_payloads(self):
        self.assertTrue(resume_confirmed(None))
        self.assertTrue(resume_confirmed(""))
        self.assertTrue(resume_confirmed({"confirmed": True}))
        self.assertFalse(resume_confirmed({"confirmed": False}))
        self.assertFalse(resume_confirmed("cancel"))

    def test_default_profile_id(self):
        self.assertEqual(default_profile_id("https://App.Example.com/path"), "auto-app.example.com")


class DatabaseUrlTests(unittest.TestCase):
    def test_remote_requires_tls(self):
        with self.assertRaises(AuthStoreError):
            validate_database_url("postgresql://user:secret@db.example.com:5432/insight_auth")

    def test_remote_require_ok(self):
        validate_database_url(
            "postgresql://user:secret@db.example.com:5432/insight_auth?sslmode=require"
        )

    def test_remote_verify_full_ok(self):
        validate_database_url(
            "postgresql://user:secret@db.example.com:5432/insight_auth?sslmode=verify-full"
        )

    def test_localhost_without_tls_ok(self):
        validate_database_url("postgresql://user:secret@localhost:5432/insight_auth")

    def test_profile_id(self):
        self.assertEqual(validate_profile_id("lumenshop"), "lumenshop")
        with self.assertRaises(AuthStoreError):
            validate_profile_id("../etc")


if __name__ == "__main__":
    unittest.main()
