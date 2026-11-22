"""Provider adapters: request shape, response parsing and error mapping, without any real network or keys."""
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from hacksolve import ai
from hacksolve.config import Config

KEY = "sk-test-0123456789abcdefghij"


def cfg(**keys):
    c = Config()
    c.keys = {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "GEMINI_API_KEY": "", **keys}
    c.ai_provider = "auto"
    return c


class Capture:
    """Replaces ai._http_json and records what would have been sent."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        return self.reply


class AdapterTests(unittest.TestCase):
    def test_anthropic_request_and_response(self):
        cap = Capture({"content": [{"type": "text", "text": "Hel"}, {"type": "text", "text": "lo"}]})
        with mock.patch.object(ai, "_http_json", cap):
            out = ai.AnthropicProvider(cfg(ANTHROPIC_API_KEY=KEY)).complete("prompt", 123, 9)
        url, headers, payload, timeout = cap.calls[0]
        self.assertEqual((out, url, timeout), ("Hello", "https://api.anthropic.com/v1/messages", 9))
        self.assertEqual((headers["x-api-key"], headers["anthropic-version"]), (KEY, "2023-06-01"))
        self.assertEqual((payload["max_tokens"], payload["messages"][0]["content"]), (123, "prompt"))
        self.assertNotIn(KEY, url)

    def test_gemini_keeps_the_key_out_of_the_url(self):
        cap = Capture({"candidates": [{"content": {"parts": [{"text": "A"}, {"text": "B"}]}}]})
        with mock.patch.object(ai, "_http_json", cap):
            out = ai.GeminiProvider(cfg(GEMINI_API_KEY="AIzaSyA1234567890abcdefghijk")).complete("p", 50, 5)
        url, headers, payload, _ = cap.calls[0]
        self.assertEqual(out, "AB")
        self.assertTrue(url.startswith("https://generativelanguage.googleapis.com/v1beta/models/"))
        self.assertNotIn("AIza", url)
        self.assertEqual(headers["x-goog-api-key"], "AIzaSyA1234567890abcdefghijk")
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 50)

    def test_openai_uses_the_right_token_parameter_per_host(self):
        cap = Capture({"choices": [{"message": {"content": "ok"}}]})
        c = cfg(OPENAI_API_KEY=KEY)
        with mock.patch.object(ai, "_http_json", cap):
            ai.OpenAIProvider(c).complete("p", 10, 5)
            c.openai_base_url = "https://openrouter.example/api/v1"
            ai.OpenAIProvider(c).complete("p", 10, 5)
        self.assertIn("max_completion_tokens", cap.calls[0][2])
        self.assertIn("max_tokens", cap.calls[1][2])
        self.assertEqual(cap.calls[0][1]["Authorization"], "Bearer " + KEY)

    def test_openai_compatible_local_server_needs_no_key_but_remote_http_is_refused(self):
        c = cfg()
        c.openai_base_url = "http://127.0.0.1:11434/v1"
        p = ai.OpenAIProvider(c)
        self.assertTrue(p.configured())
        cap = Capture({"choices": [{"message": {"content": "ok"}}]})
        with mock.patch.object(ai, "_http_json", cap):
            p.complete("p", 10, 5)
        self.assertNotIn("Authorization", cap.calls[0][1])
        c.openai_base_url = "http://example.com/v1"
        self.assertFalse(ai.OpenAIProvider(c).configured())
        c.openai_base_url = "https://api.openai.com/v1"
        self.assertFalse(ai.OpenAIProvider(c).configured())

    def test_malformed_provider_replies_become_clean_errors(self):
        for prov, bad in ((ai.AnthropicProvider(cfg(ANTHROPIC_API_KEY=KEY)), {}), (ai.GeminiProvider(cfg(GEMINI_API_KEY="k")), {"candidates": []}),
                          (ai.OpenAIProvider(cfg(OPENAI_API_KEY=KEY)), {"choices": []})):
            with mock.patch.object(ai, "_http_json", Capture(bad)):
                with self.assertRaises(ai.AIError) as cm:
                    prov.complete("p", 10, 5)
            self.assertEqual(cm.exception.code, "bad_response")

    def test_provider_selection_rules(self):
        self.assertEqual(ai.pick(cfg()).id, "local")
        self.assertEqual(ai.pick(cfg(ANTHROPIC_API_KEY=KEY)).id, "anthropic")
        self.assertEqual(ai.pick(cfg(GEMINI_API_KEY="k")).id, "gemini")
        self.assertEqual(ai.pick(cfg(ANTHROPIC_API_KEY=KEY), "local").id, "local")
        for name in ("anthropic", "openai", "gemini"):
            with self.assertRaises(ai.AIError) as cm:
                ai.pick(cfg(), name)
            self.assertEqual(cm.exception.code, "not_configured")
        c = cfg()
        c.ai_provider = "gemini"
        with self.assertRaises(ai.AIError):
            ai.pick(c)
        status = {p["id"]: p["configured"] for p in ai.status(cfg(OPENAI_API_KEY=KEY))}
        self.assertEqual(status, {"local": True, "anthropic": False, "openai": True, "gemini": False})


class WireTests(unittest.TestCase):
    """_http_json against a real local socket: status mapping, timeouts, and key hygiene."""

    @classmethod
    def setUpClass(cls):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers["Content-Length"])
                self.rfile.read(n)
                code = int(self.path.strip("/") or 200)
                if code == 999:
                    time.sleep(2)
                    code = 200
                body = (b'{"error":"bad model ' + KEY.encode() + b'"}') if code == 400 else b'{"ok": true}' if code == 200 else b"{}"
                if code == 201:
                    body = b"not json"
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def code(self, path, timeout=5):
        try:
            return ai._http_json(self.base + path, {}, {"x": 1}, timeout)
        except ai.AIError as e:
            return e

    def test_http_statuses_map_to_error_codes(self):
        self.assertEqual(self.code("/200"), {"ok": True})
        self.assertEqual(self.code("/401").code, "auth")
        self.assertEqual(self.code("/403").code, "auth")
        self.assertEqual(self.code("/429").code, "rate_limited")
        self.assertEqual(self.code("/500").code, "upstream")
        self.assertEqual(self.code("/201").code, "bad_response")

    def test_timeouts_and_unreachable_hosts_are_reported(self):
        self.assertEqual(self.code("/999", timeout=0.3).code, "timeout")
        with self.assertRaises(ai.AIError) as cm:
            ai._http_json("http://127.0.0.1:1/x", {}, {}, 2)
        self.assertIn(cm.exception.code, ("network", "timeout"))

    def test_error_text_never_contains_the_api_key(self):
        e = self.code("/400")
        self.assertEqual(e.code, "upstream")
        self.assertNotIn(KEY, e.message)
        self.assertIn("[redacted]", e.message)


if __name__ == "__main__":
    unittest.main()
