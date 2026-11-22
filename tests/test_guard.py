import shutil
import unittest

from hacksolve import ai, guard
from helpers import GOOD_APP

PAGE = "<!doctype html><html><body>{}</body></html>"


class GuardTests(unittest.TestCase):
    def flags(self, body):
        return guard.scan_html(PAGE.format(body))

    def test_clean_app_and_demo_workbench_pass(self):
        self.assertEqual(guard.scan_html(GOOD_APP), [])
        spec = ai.local_extract("Title line here\nBuild a tool for farmers.\n- Must work offline.\nDeliverables: prototype.")
        self.assertEqual(guard.scan_html(ai.demo_app(spec)), [])

    def test_blocks_dangerous_javascript(self):
        for js in ("eval('1')", "new Function('x')", "fetch('/a')", "new XMLHttpRequest()", "new WebSocket('ws://x')",
                   "navigator.sendBeacon('/x')", "document.cookie='a=b'", "window.open('x')", "setTimeout('alert(1)',1)",
                   "parent.postMessage('x','*')", "top.location='x'", "import('x')"):
            self.assertTrue(self.flags(f"<script>{js}</script>"), js)

    def test_blocks_dangerous_markup_and_external_resources(self):
        for body in ('<iframe src="x"></iframe>', '<script src="https://evil.example/x.js"></script>', '<img src="//evil.example/a.png">',
                     '<link rel="stylesheet" href="http://evil.example/a.css">', '<meta http-equiv="refresh" content="0;url=x">',
                     '<a href="javascript:alert(1)">x</a>', '<style>@import url(x.css);</style>', '<div style="background:url(https://x.test/a.png)"></div>',
                     '<button onclick="fetch(\'/x\')">x</button>', "<base href='x'>", "\u202etrojan"):
            self.assertTrue(self.flags(body), body)

    def test_plain_words_in_visible_text_are_not_false_positives(self):
        self.assertEqual(self.flags("<button>Import data</button><p>Open the top panel and the parent form</p><script>const a=1;</script>"), [])

    def test_detects_truncated_and_non_html_output(self):
        self.assertIn("Output was cut off before </html>", guard.scan_html("<!doctype html><html><body>hi"))
        self.assertIn("Not an HTML document", guard.scan_html("just some text"))

    def test_requirement_coverage_comments_are_parsed(self):
        cov = guard.coverage("<!-- REQ-1: works offline --><!--req-2:flags risk-->")
        self.assertEqual(cov["REQ-1"], "works offline")
        self.assertEqual(cov["REQ-2"], "flags risk")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_syntax_check(self):
        self.assertEqual(guard.check_js_syntax("<html><script>let a = 1;</script></html>")[0], "ok")
        status, msg = guard.check_js_syntax("<html><script>let a = ;</script></html>")
        self.assertEqual(status, "fail")
        self.assertNotIn("/tmp", msg)

    def test_local_engine_extracts_facts_and_flags_unknowns(self):
        s = ai.local_extract("Bus Tool\nBuild a route ranking tool for city commuters.\n- Must run offline.\nSubmit a prototype by 12 Dec 2026.")
        self.assertEqual(s["deadline"], "2026-12-12")
        self.assertTrue(any("judging" in r.lower() for r in s["risks"]))
        self.assertTrue(s["requirements"][0]["id"].startswith("REQ-"))

    def test_normalize_spec_rejects_junk_and_clamps_values(self):
        with self.assertRaises(ai.AIError):
            ai.normalize_spec(["not", "a", "dict"])
        with self.assertRaises(ai.AIError):
            ai.normalize_spec({"requirements": []})
        s = ai.normalize_spec({"title": "T" * 999, "requirements": [{"text": "a"}, 5, {"text": "b"}], "judge": {"clarity": 99}})
        self.assertEqual(len(s["title"]), 120)
        self.assertEqual([r["id"] for r in s["requirements"]], ["REQ-1", "REQ-2"])
        self.assertEqual(s["judge"]["clarity"], 5)

    def test_json_reply_parser_handles_chatter_around_json(self):
        self.assertEqual(ai.parse_json_reply('Sure! {"a": 1} hope that helps')["a"], 1)
        with self.assertRaises(ai.AIError):
            ai.parse_json_reply("no json here")


if __name__ == "__main__":
    unittest.main()
