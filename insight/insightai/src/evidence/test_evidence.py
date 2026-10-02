"""Unit tests for evidence helpers (no live S3 / MCP)."""

from __future__ import annotations

import unittest

from evidence.capture import _cap, _filter_console, _filter_network, looks_like_login_page
from evidence.context import begin_step, clear_step, next_seq, record_artifact, take_artifacts
from evidence.session import normalize_thread_id


class LoginDetectTests(unittest.TestCase):
    def test_login_page(self):
        self.assertTrue(
            looks_like_login_page("Email address\nPassword\nSign in")
        )
        self.assertFalse(looks_like_login_page("Add to cart\nSubtotal $12"))


class FilterTests(unittest.TestCase):
    def test_console_prefers_errors(self):
        text = "info ok\nError: boom\nwarn\n" + "\n".join(f"line{i}" for i in range(40))
        filtered = _filter_console(text)
        self.assertIn("Error: boom", filtered)

    def test_network_keeps_4xx(self):
        text = "GET /ok 200\nPOST /pay 500\nGET /x failed"
        filtered = _filter_network(text)
        self.assertIn("500", filtered)
        self.assertIn("failed", filtered)

    def test_cap_utf8(self):
        big = "x" * 100_000
        out = _cap(big, limit=1000)
        self.assertIsInstance(out, str)
        self.assertLessEqual(len(out.encode("utf-8")), 1000)


class ContextTests(unittest.TestCase):
    def tearDown(self):
        clear_step()

    def test_artifacts(self):
        begin_step("t1", 2)
        self.assertEqual(next_seq(), 1)
        record_artifact(
            {"kind": "screenshot", "bucket": "insight-screenshots", "object_key": "a.png"}
        )
        arts = take_artifacts()
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["object_key"], "a.png")
        self.assertEqual(take_artifacts(), [])


class ThreadIdTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_thread_id(None), "local")
        self.assertEqual(normalize_thread_id("ab/cd ef"), "ab-cd-ef")


if __name__ == "__main__":
    unittest.main()
