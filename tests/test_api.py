import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from hacksolve.analysis import STAGES
from hacksolve.db import now, uid
from helpers import INJECTED, PW, Env, FakeProvider, make_docx, make_pdf

ROOT = Path(__file__).resolve().parent.parent
PID = "0" * 32


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_register_login_me_logout_flow(self):
        c = self.env.client()
        email = c.signup("Maya Chen")
        sc = c.call("GET", "/api/auth/me")
        self.assertEqual((sc.status, sc.data["user"]["email"], sc.data["user"]["name"]), (200, email, "Maya Chen"))
        self.assertNotIn("password", sc.raw.decode().lower())
        self.assertEqual(c.call("POST", "/api/auth/logout").status, 204)
        self.assertEqual(c.call("GET", "/api/auth/me").status, 401)
        r = c.call("POST", "/api/auth/login", {"email": email.upper(), "password": PW})
        self.assertEqual(r.status, 200)
        self.assertEqual(c.call("GET", "/api/auth/me").status, 200)

    def test_session_cookie_is_httponly_samesite_strict(self):
        c = self.env.client()
        r = c.call("POST", "/api/auth/register", {"name": "A", "email": "cookie@example.com", "password": PW})
        cookie = r.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)

    def test_wrong_password_and_unknown_email_look_identical(self):
        c = self.env.client()
        email = c.signup()
        a = c.call("POST", "/api/auth/login", {"email": email, "password": "WrongPassword#1"})
        b = c.call("POST", "/api/auth/login", {"email": "nobody@example.com", "password": "WrongPassword#1"})
        self.assertEqual((a.status, b.status), (401, 401))
        self.assertEqual(a.error, b.error)
        kinds = [e["kind"] for e in self.env.app.db.all("SELECT kind FROM security_events")]
        self.assertIn("login_failed", kinds)

    def test_duplicate_email_is_rejected_case_insensitively(self):
        c = self.env.client()
        email = c.signup()
        r = self.env.client().call("POST", "/api/auth/register", {"name": "Z", "email": email.upper(), "password": PW})
        self.assertEqual((r.status, r.error["code"]), (409, "email_taken"))

    def test_validation_errors_list_every_problem(self):
        c = self.env.client()
        r = c.call("POST", "/api/auth/register", {})
        self.assertEqual(r.status, 422)
        self.assertEqual(set(r.error["details"]), {"name", "email", "password"})
        r = c.call("POST", "/api/auth/register", {"name": "A", "email": "not-an-email", "password": "short"})
        self.assertIn("email", r.error["details"])
        r = c.call("POST", "/api/auth/register", {"name": "A", "email": "weak@example.com", "password": "password123"})
        self.assertIn("password", r.error["details"])
        self.assertEqual(c.call("POST", "/api/auth/register", body=b"{not json", ).status, 400)
        self.assertEqual(c.call("POST", "/api/auth/register", body=b"{}", ctype="text/plain").status, 415)
        self.assertEqual(c.call("POST", "/api/auth/register", {"name": "x" * 300000}).status, 413)
        self.assertEqual(c.call("POST", "/api/auth/register", body=b"[" * 150000).status, 400)      # nesting bomb: 400, not a crash

    def test_csrf_header_and_origin_are_enforced(self):
        c = self.env.client()
        body = {"name": "A", "email": "csrf@example.com", "password": PW}
        self.assertEqual(c.call("POST", "/api/auth/register", body, csrf=False).status, 403)
        r = c.call("POST", "/api/auth/register", body, headers={"Origin": "http://evil.example"})
        self.assertEqual((r.status, r.error["code"]), (403, "csrf_blocked"))
        self.assertIn("csrf_blocked", [e["kind"] for e in self.env.app.db.all("SELECT kind FROM security_events")])
        ok = c.call("POST", "/api/auth/register", body, headers={"Origin": f"http://127.0.0.1:{self.env.port}"})
        self.assertEqual(ok.status, 201)

    def test_every_protected_route_requires_login(self):
        c = self.env.client()
        for method, path in [("GET", "/api/auth/me"), ("GET", "/api/projects"), ("POST", "/api/projects"), ("GET", f"/api/projects/{PID}"),
                             ("PATCH", f"/api/projects/{PID}"), ("DELETE", f"/api/projects/{PID}"), ("POST", f"/api/projects/{PID}/brief"),
                             ("POST", f"/api/projects/{PID}/files"), ("POST", f"/api/projects/{PID}/analyze"), ("GET", f"/api/projects/{PID}/analysis"),
                             ("GET", f"/api/projects/{PID}/analysis/status"), ("GET", f"/api/projects/{PID}/preview"), ("GET", f"/api/projects/{PID}/checklist"),
                             ("GET", f"/api/projects/{PID}/export"), ("DELETE", f"/api/projects/{PID}/artifacts"), ("PATCH", f"/api/analysis/{PID}"),
                             ("POST", f"/api/analysis/{PID}/regenerate"), ("PATCH", f"/api/checklist/{PID}"), ("GET", "/api/providers")]:
            self.assertEqual(c.call(method, path, {} if method != "GET" else None).status, 401, f"{method} {path}")

    def test_expired_session_is_rejected(self):
        c = self.env.client()
        c.signup()
        self.env.app.db.run("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(c.call("GET", "/api/auth/me").status, 401)

    def test_security_headers_and_unknown_routes(self):
        c = self.env.client()
        r = c.call("GET", "/api/health")
        for h in ("x-content-type-options", "x-frame-options", "content-security-policy", "referrer-policy"):
            self.assertIn(h, r.headers)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(c.call("GET", "/api/nope").status, 404)
        self.assertEqual(c.call("GET", "/../hacksolve/config.py").status, 404)
        self.assertEqual(c.call("GET", "/%2e%2e/hacksolve/config.py").status, 404)

    def test_profile_preferences_update(self):
        c = self.env.client()
        c.signup()
        r = c.call("PATCH", "/api/auth/me", {"name": "New Name", "preferences": {"theme": "dark"}})
        self.assertEqual((r.data["user"]["name"], r.data["user"]["preferences"]), ("New Name", {"theme": "dark"}))


class StaticTests(unittest.TestCase):
    """First paint must never depend on another website (a slow font host once cost 10 seconds)."""

    @classmethod
    def setUpClass(cls):
        cls.env = Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def no_match(self, rx, text):
        m = re.search(rx, text)
        self.assertIsNone(m, m and m.group(0)[:80])

    def test_page_has_no_third_party_requests_or_inline_scripts(self):
        c = self.env.client()
        page = c.call("GET", "/")
        self.assertEqual((page.status, page.headers["content-type"]), (200, "text/html; charset=utf-8"))
        html = page.raw.decode()
        self.no_match(r"""(?:href|src)=["']\s*(?:https?:)?//""", html)
        self.no_match(r"<script(?![^>]*\bsrc=)[^>]*>", html)
        css = c.call("GET", "/app.css").raw.decode()
        self.no_match(r"@import|url\(\s*[\"']?(?:https?:)?//", css)
        js = c.call("GET", "/app.js").raw.decode()
        self.no_match(r"""(?:fetch|\.open)\(\s*["'`]https?://|(?:src|href|action)=\\?["']https?://""", js)
        csp = page.headers["content-security-policy"]
        for host in ("googleapis", "gstatic", "cdn", "unpkg", "jsdelivr"):
            self.assertNotIn(host, csp)
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-eval", csp)

    def test_theme_blocks_in_the_stylesheet_are_intact(self):
        css = self.env.client().call("GET", "/app.css").raw.decode()
        self.assertEqual(css.count("{"), css.count("}"))
        light = re.search(r"^:root\{([^}]*)\}", css, re.M)
        dark = re.search(r"^:root\[data-theme=dark\]\{([^}]*)\}", css, re.M)
        self.assertTrue(light and dark, "both theme blocks must exist")
        vars_ = lambda block: dict(re.findall(r"(--[\w-]+):([^;]+);?", block))
        lv, dv = vars_(light.group(1)), vars_(dark.group(1))
        colors = ["--bg", "--card", "--ink", "--mut", "--line", "--acc", "--on", "--accs", "--mint", "--amb", "--rose", "--sh"]
        for name in colors:
            self.assertIn(name, lv, f"light theme lacks {name}")
            self.assertIn(name, dv, f"dark theme lacks {name}")
        for name in ("--bg", "--card", "--ink", "--mut", "--line"):
            self.assertNotEqual(lv[name], dv[name], f"{name} is identical in both themes")
        self.assertNotIn("color-scheme", light.group(1))
        self.assertIn("color-scheme:dark", dark.group(1))
        self.assertIn("--se", lv)
        self.assertIn("--sa", lv)

    def test_static_files_support_conditional_requests(self):
        c = self.env.client()
        first = c.call("GET", "/app.js")
        etag = first.headers["etag"]
        again = c.call("GET", "/app.js", headers={"If-None-Match": etag})
        self.assertEqual((again.status, again.raw), (304, b""))
        self.assertEqual(c.call("GET", "/app.js", headers={"If-None-Match": '"stale"'}).status, 200)

    def test_security_headers_present_on_static_pages(self):
        r = self.env.client().call("GET", "/")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_keep_alive_survives_a_delete_with_a_body(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.env.port, timeout=10)
        c.request("DELETE", f"/api/projects/{PID}", body=b"unexpected body", headers={"X-Requested-With": "HackSolve"})
        first = c.getresponse()
        first.read()
        c.request("GET", "/api/health")
        second = c.getresponse()
        self.assertEqual((first.status, second.status), (401, 200))
        c.close()

    def test_head_requests_work_without_a_body(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.env.port, timeout=10)
        c.request("HEAD", "/")
        r = c.getresponse()
        self.assertEqual((r.status, r.read(), r.getheader("Content-Type")), (200, b"", "text/html; charset=utf-8"))
        self.assertGreater(int(r.getheader("Content-Length")), 100)
        c.close()

    def test_busy_port_falls_back_to_the_next_free_one(self):
        import socket
        from hacksolve.server import make_server
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        busy = blocker.getsockname()[1]
        try:
            self.env.cfg.port = busy
            srv = make_server(self.env.cfg)
            self.assertGreater(srv.server_address[1], busy)
            srv.server_close()
            srv.app.analysis.pool.shutdown(wait=False)
        finally:
            blocker.close()

    def test_uploaded_files_are_stored_byte_for_byte(self):
        import hashlib
        fixture = (ROOT / "tests" / "fixtures" / "chromium.pdf").read_bytes()
        self.assertIn(b"\n", fixture)
        c = self.env.client()
        c.signup()
        pid = c.project(brief=None)
        r = c.call("POST", f"/api/projects/{pid}/files", raw=fixture, headers={"X-Filename": "chromium.pdf"})
        self.assertEqual(r.status, 201, r.raw)
        stored = next((self.env.cfg.upload_dir / pid).iterdir()).read_bytes()
        row = self.env.app.db.one("SELECT checksum, size FROM file_uploads WHERE project_id=?", (pid,))
        self.assertEqual((stored, row["size"]), (fixture, len(fixture)))
        self.assertEqual(row["checksum"], hashlib.sha256(fixture).hexdigest())
        self.assertIn("Offline Clinic Triage Assistant", r.data["brief"]["text"])


class RateLimitTests(unittest.TestCase):
    def test_login_attempts_are_rate_limited(self):
        env = Env(rate_auth=3)
        try:
            c = env.client()
            codes = [c.call("POST", "/api/auth/login", {"email": "a@example.com", "password": "Whatever#12345"}).status for _ in range(4)]
            self.assertEqual(codes, [401, 401, 401, 429])
            r = c.call("POST", "/api/auth/login", {"email": "a@example.com", "password": "x"})
            self.assertIn("retry-after", r.headers)
        finally:
            env.close()

    def test_analysis_requests_are_rate_limited(self):
        env = Env(rate_analyze=2)
        try:
            c = env.client()
            c.signup()
            pid = c.project()
            codes = []
            for _ in range(3):
                codes.append(c.call("POST", f"/api/projects/{pid}/analyze", {"provider": "local"}).status)
                c.solve  # no-op, keeps flake out: wait for the run to finish below
                import time; time.sleep(0.4)
            self.assertEqual(codes[-1], 429)
        finally:
            env.close()


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env(max_upload_bytes=100_000)

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_project_crud(self):
        c = self.env.client()
        c.signup()
        r = c.call("POST", "/api/projects", {"title": "Hack 2026", "deadline": "2026-12-05", "competition_url": "https://example.org/hack"})
        self.assertEqual((r.status, r.data["project"]["status"]), (201, "draft"))
        pid = r.data["project"]["id"]
        self.assertEqual(len(c.call("GET", "/api/projects").data["projects"]), 1)
        g = c.call("GET", f"/api/projects/{pid}").data
        self.assertEqual((g["project"]["title"], g["brief"], g["requirements"], g["analysis"]), ("Hack 2026", None, [], None))
        p = c.call("PATCH", f"/api/projects/{pid}", {"title": "Renamed", "deadline": None})
        self.assertEqual((p.data["project"]["title"], p.data["project"]["deadline"]), ("Renamed", None))
        self.assertEqual(c.call("PATCH", f"/api/projects/{pid}", {"deadline": "05/12/2026"}).status, 422)
        self.assertEqual(c.call("POST", "/api/projects", {"title": "x" * 200}).status, 422)
        self.assertEqual(c.call("POST", "/api/projects", {"title": "ok", "competition_url": "javascript:alert(1)"}).status, 422)
        self.assertEqual(c.call("DELETE", f"/api/projects/{pid}").status, 204)
        self.assertEqual(c.call("GET", f"/api/projects/{pid}").status, 404)

    def test_users_cannot_touch_each_others_projects(self):
        a, b = self.env.client(), self.env.client()
        a.signup()
        b.signup()
        pid = a.project()
        a.solve(pid)
        item = a.call("GET", f"/api/projects/{pid}/checklist").data["checklist"][0]["id"]
        aid = a.call("GET", f"/api/projects/{pid}/analysis").data["completed"]["id"]
        for method, path, body in [("GET", f"/api/projects/{pid}", None), ("PATCH", f"/api/projects/{pid}", {"title": "pwn"}),
                                   ("DELETE", f"/api/projects/{pid}", None), ("POST", f"/api/projects/{pid}/brief", {"text": "x" * 40}),
                                   ("POST", f"/api/projects/{pid}/analyze", {}), ("GET", f"/api/projects/{pid}/analysis", None),
                                   ("GET", f"/api/projects/{pid}/analysis/status", None), ("GET", f"/api/projects/{pid}/preview", None),
                                   ("GET", f"/api/projects/{pid}/checklist", None), ("GET", f"/api/projects/{pid}/export", None),
                                   ("DELETE", f"/api/projects/{pid}/artifacts", None), ("PATCH", f"/api/checklist/{item}", {"status": "done"}),
                                   ("PATCH", f"/api/analysis/{aid}", {"explanation": "pwn"}), ("POST", f"/api/analysis/{aid}/regenerate", {})]:
            self.assertEqual(b.call(method, path, body).status, 404, f"{method} {path}")
        self.assertEqual(b.call("GET", "/api/projects").data["projects"], [])
        self.assertEqual(a.call("GET", f"/api/projects/{pid}").data["project"]["title"], "My project")

    def test_brief_validation_and_injection_logging(self):
        c = self.env.client()
        c.signup()
        pid = c.project(brief=None)
        self.assertEqual(c.call("POST", f"/api/projects/{pid}/brief", {"text": "too short"}).status, 422)
        r = c.call("POST", f"/api/projects/{pid}/brief", {"text": INJECTED + "\u200b\u202e"})
        self.assertEqual(r.status, 201)
        flags = r.data["brief"]["injection_flags"]
        self.assertEqual(len(flags), 1)
        self.assertNotIn("\u202e", r.data["brief"]["text"])
        ev = self.env.app.db.all("SELECT kind, detail FROM security_events WHERE kind='prompt_injection'")
        self.assertTrue(ev)

    def test_pdf_and_docx_uploads_become_briefs(self):
        c = self.env.client()
        c.signup()
        pid = c.project(brief=None)
        lines = ["Rural Clinic Triage", "Build an offline triage helper for health workers.", "Must never transmit patient data."]
        r = c.call("POST", f"/api/projects/{pid}/files", raw=make_pdf(lines, compress=True), headers={"X-Filename": "brief.pdf"})
        self.assertEqual(r.status, 201, r.raw)
        self.assertIn("offline triage helper", r.data["brief"]["text"])
        self.assertEqual(r.data["brief"]["source_type"], "file")
        r = c.call("POST", f"/api/projects/{pid}/files", raw=make_docx(lines), headers={"X-Filename": "brief.docx"})
        self.assertEqual(r.status, 201, r.raw)
        self.assertIn("Must never transmit patient data.", r.data["brief"]["text"])
        self.assertEqual(len(c.call("GET", f"/api/projects/{pid}").data["files"]), 2)

    def test_upload_storage_ignores_user_filename_and_is_cleaned_on_delete(self):
        c = self.env.client()
        c.signup()
        pid = c.project(brief=None)
        r = c.call("POST", f"/api/projects/{pid}/files", raw=b"Build a bus tool that ranks routes.", headers={"X-Filename": "..%2F..%2Fetc%2Fpasswd.txt"})
        self.assertEqual((r.status, r.data["file"]["filename"]), (201, "passwd.txt"))
        stored = list((self.env.cfg.upload_dir / pid).iterdir())
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].suffix, ".txt")
        self.assertNotIn("passwd", stored[0].name)
        c.call("DELETE", f"/api/projects/{pid}")
        self.assertFalse((self.env.cfg.upload_dir / pid).exists())

    def test_malicious_and_invalid_uploads_are_rejected_and_logged(self):
        c = self.env.client()
        c.signup()
        pid = c.project(brief=None)
        up = lambda data, name, **kw: c.call("POST", f"/api/projects/{pid}/files", raw=data, headers={"X-Filename": name}, **kw)
        self.assertEqual(up(b"Not a pdf at all, just text", "x.pdf").status, 422)
        self.assertEqual(up(b"MZ\x90\x00 pretending to be text", "x.txt").status, 422)
        self.assertEqual(up(b"hello hello hello", "x.exe").status, 415)
        self.assertEqual(up(b"a" * 120_000, "big.txt").status, 413)
        self.assertEqual(up(make_docx(["x" * 20], extra={"word/vbaProject.bin": b"0"}), "m.docx").status, 422)
        self.assertEqual(up(b"Build a tool for farmers please", "ok.txt", ctype="text/plain").status, 415)
        kinds = [e["kind"] for e in self.env.app.db.all("SELECT kind FROM security_events")]
        self.assertGreaterEqual(kinds.count("upload_rejected"), 4)
        self.assertEqual(len(c.call("GET", f"/api/projects/{pid}").data["files"]), 0)


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_full_demo_pipeline_end_to_end(self):
        c = self.env.client()
        c.signup()
        pid = c.project("Clinic")
        s = c.solve(pid)
        self.assertEqual((s["status"], s["progress"], s["mode"]), ("completed", 100, "demo"))
        self.assertEqual([x["key"] for x in s["stages"]], [k for k, _ in STAGES])
        self.assertTrue(all(x["status"] in ("done", "skipped") for x in s["stages"]), s["stages"])
        d = c.call("GET", f"/api/projects/{pid}/analysis").data
        res = d["completed"]
        self.assertTrue(res["result"]["checks"]["ok"])
        self.assertTrue(res["artifact"]["content"].startswith("<!doctype html"))
        self.assertGreaterEqual(len(d["requirements"]), 3)
        self.assertEqual(res["model_used"], "local:local-rules-v1")
        self.assertIn("I am planning to build", res["explanation"])
        self.assertIn("<brief>", res["implementation_prompt"])
        self.assertEqual(res["result"]["deadline"], "2026-12-05")
        proj = c.call("GET", f"/api/projects/{pid}").data
        self.assertEqual((proj["project"]["status"], proj["project"]["deadline"]), ("plan_ready", "2026-12-05"))
        self.assertGreaterEqual(len(proj["checklist"]), 6)

    def test_preview_is_served_with_a_locked_down_sandbox(self):
        c = self.env.client()
        c.signup()
        pid = c.project()
        c.solve(pid)
        r = c.call("GET", f"/api/projects/{pid}/preview")
        csp = r.headers["content-security-policy"]
        self.assertEqual(r.status, 200)
        for want in ("sandbox allow-scripts", "connect-src 'none'", "default-src 'none'", "form-action 'none'"):
            self.assertIn(want, csp)
        self.assertEqual(r.headers["x-frame-options"], "SAMEORIGIN")
        self.assertIn("hsErr", c.call("GET", f"/api/projects/{pid}/preview?probe=1").raw.decode())
        self.assertNotIn("hsErr", r.raw.decode())

    def test_checklist_drives_project_status(self):
        c = self.env.client()
        c.signup()
        pid = c.project()
        c.solve(pid)
        items = c.call("GET", f"/api/projects/{pid}/checklist").data["checklist"]
        for it in items:
            r = c.call("PATCH", f"/api/checklist/{it['id']}", {"status": "done"})
            self.assertEqual(r.data["item"]["status"], "done")
        card = c.call("GET", f"/api/projects/{pid}").data["project"]
        self.assertEqual((card["status"], card["progress"]), ("ready_to_build", 100))
        extra = c.call("POST", f"/api/projects/{pid}/checklist", {"title": "Book the demo room"})
        self.assertEqual(extra.status, 201)
        self.assertEqual(c.call("GET", f"/api/projects/{pid}").data["project"]["status"], "plan_ready")
        self.assertEqual(c.call("PATCH", f"/api/checklist/{items[0]['id']}", {"status": "maybe"}).status, 422)

    def test_edit_explanation_and_requirement_status(self):
        c = self.env.client()
        c.signup()
        pid = c.project()
        c.solve(pid)
        d = c.call("GET", f"/api/projects/{pid}/analysis").data
        aid, req = d["completed"]["id"], d["requirements"][0]["id"]
        r = c.call("PATCH", f"/api/analysis/{aid}", {"explanation": "My own words.", "requirements": [{"id": req, "status": "done"}]})
        self.assertEqual((r.data["explanation"], r.data["explanation_version"]), ("My own words.", 2))
        d = c.call("GET", f"/api/projects/{pid}/analysis").data
        self.assertEqual(d["requirements"][0]["status"], "done")
        self.assertEqual(c.call("PATCH", f"/api/analysis/{aid}", {"requirements": [{"id": req, "status": "bogus"}]}).status, 422)

    def test_export_regenerate_and_delete_artifacts(self):
        c = self.env.client()
        c.signup()
        pid = c.project("Export me")
        c.solve(pid)
        r = c.call("GET", f"/api/projects/{pid}/export")
        self.assertIn("attachment", r.headers["content-disposition"])
        ex = r.data
        self.assertEqual(ex["project"]["title"], "Export me")
        self.assertTrue(ex["artifacts"][0]["content"].startswith("<!doctype html"))
        self.assertTrue(ex["briefs"] and ex["checklist"] and ex["analyses"][0]["result"])
        aid = c.call("GET", f"/api/projects/{pid}/analysis").data["completed"]["id"]
        self.assertEqual(c.call("POST", f"/api/analysis/{aid}/regenerate", {"provider": "local"}).status, 202)
        end = __import__("time").time() + 20
        while c.call("GET", f"/api/projects/{pid}/analysis/status").data["analysis"]["status"] != "completed" and __import__("time").time() < end:
            __import__("time").sleep(0.05)
        self.assertEqual(c.call("DELETE", f"/api/projects/{pid}/artifacts").status, 204)
        d = c.call("GET", f"/api/projects/{pid}/analysis").data
        self.assertEqual((d["completed"], d["requirements"], d["latest"]), (None, [], None))
        self.assertEqual(c.call("GET", f"/api/projects/{pid}").data["project"]["status"], "draft")
        self.assertEqual(c.call("GET", f"/api/projects/{pid}/preview").status, 404)
        self.assertIsNotNone(c.call("GET", f"/api/projects/{pid}").data["brief"])

    def test_analyze_preconditions(self):
        c = self.env.client()
        c.signup()
        empty = c.project(brief=None)
        self.assertEqual(c.call("POST", f"/api/projects/{empty}/analyze", {}).error["code"], "no_brief")
        pid = c.project()
        r = c.call("POST", f"/api/projects/{pid}/analyze", {"provider": "anthropic"})
        self.assertEqual((r.status, r.error["code"]), (422, "provider_not_configured"))
        self.assertEqual(c.call("POST", f"/api/projects/{pid}/analyze", {"provider": "skynet"}).status, 422)
        self.env.app.db.run("INSERT INTO analyses(id,project_id,status,created_at) VALUES(?,?,?,?)", (uid(), pid, "running", now()))
        self.assertEqual(c.call("POST", f"/api/projects/{pid}/analyze", {"provider": "local"}).error["code"], "analysis_running")
        self.assertEqual(c.call("POST", f"/api/projects/{pid}/analysis/cancel").status, 200)

    def test_cancelled_analysis_keeps_brief_and_marks_cancelled(self):
        c = self.env.client()
        c.signup()
        pid = c.project()
        aid = uid()
        stages = json.dumps([{"key": k, "label": l, "status": "pending", "detail": ""} for k, l in STAGES])
        self.env.app.db.run("INSERT INTO analyses(id,project_id,status,stages_json,cancel_requested,created_at) VALUES(?,?,?,?,1,?)", (aid, pid, "queued", stages, now()))
        self.env.app.analysis.run_inline(aid, "local")
        s = self.env.app.analysis.summary(aid)
        self.assertEqual(s["status"], "cancelled")
        self.assertIsNotNone(c.call("GET", f"/api/projects/{pid}").data["brief"])

    def test_injected_brief_is_flagged_and_never_reaches_the_plan(self):
        c = self.env.client()
        c.signup()
        pid = c.project("Injected", brief=INJECTED)
        s = c.solve(pid)
        self.assertEqual(s["status"], "completed")
        self.assertIn("ignored 1 instruction-like", s["stages"][0]["detail"])
        blob = json.dumps(c.call("GET", f"/api/projects/{pid}/analysis").data).lower()
        self.assertNotIn("169.254", blob)
        self.assertNotIn("system prompt", blob)

    def test_providers_endpoint_reports_status_without_secrets(self):
        c = self.env.client()
        c.signup()
        r = c.call("GET", "/api/providers")
        ids = {p["id"]: p["configured"] for p in r.data["providers"]}
        self.assertEqual((ids["local"], ids["anthropic"]), (True, False))
        self.assertEqual(r.data["default"], "local")


class MisconfiguredProviderTests(unittest.TestCase):
    def test_provider_without_a_key_is_reported_not_a_crash(self):
        env = Env(ai_provider="gemini")
        try:
            c = env.client()
            c.signup()
            r = c.call("GET", "/api/providers")
            self.assertEqual((r.status, r.data["default"]), (200, None))
            pid = c.project()
            a = c.call("POST", f"/api/projects/{pid}/analyze", {})
            self.assertEqual((a.status, a.error["code"]), (422, "provider_not_configured"))
            self.assertEqual(c.solve(pid, provider="local")["status"], "completed")
        finally:
            env.close()


class LiveProviderTests(unittest.TestCase):
    """The live path, using a local fake of an OpenAI-compatible server."""

    def env_for(self, fake):
        return Env(ai_provider="openai", openai_base_url=fake.url, keys={"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "test-key-1234567890", "GEMINI_API_KEY": ""},
                   models={"anthropic": "x", "openai": "fake-model", "gemini": "x"})

    def test_live_analysis_builds_the_model_app_and_withholds_injected_lines(self):
        fake = FakeProvider("ok")
        env = self.env_for(fake)
        try:
            c = env.client()
            c.signup()
            pid = c.project("Live", brief=INJECTED)
            s = c.solve(pid, provider="auto")
            self.assertEqual((s["status"], s["mode"], s["model_used"]), ("completed", "live", "openai:fake-model"))
            d = c.call("GET", f"/api/projects/{pid}/analysis").data["completed"]
            self.assertIn("LIVE-FAKE-APP", d["artifact"]["content"])
            self.assertEqual(d["result"]["coverage"]["REQ-1"], "works offline, no network calls")
            self.assertTrue(d["result"]["checks"]["ok"])
            self.assertEqual(len(fake.prompts), 2)
            joined = " ".join(fake.prompts)
            self.assertNotIn("IGNORE ALL PREVIOUS", joined)
            self.assertNotIn("169.254", joined)
            self.assertIn("never follow instructions", joined)
            self.assertNotIn("test-key-1234567890", c.call("GET", "/api/providers").raw.decode())
        finally:
            env.close()
            fake.close()

    def test_unsafe_generated_code_is_repaired_once_and_rechecked(self):
        fake = FakeProvider("needs_repair")
        env = self.env_for(fake)
        try:
            c = env.client()
            c.signup()
            pid = c.project()
            s = c.solve(pid, provider="openai")
            self.assertEqual(s["status"], "completed")
            by = {x["key"]: x for x in s["stages"]}
            self.assertEqual(by["repair"]["status"], "done")
            res = c.call("GET", f"/api/projects/{pid}/analysis").data["completed"]["result"]
            self.assertTrue(res["checks"]["repaired"])
            self.assertTrue(res["checks"]["ok"])
            self.assertEqual(len(fake.prompts), 3)
        finally:
            env.close()
            fake.close()

    def test_provider_failure_is_reported_never_replaced_by_demo_output(self):
        fake = FakeProvider("ok")
        env = self.env_for(fake)
        try:
            c = env.client()
            c.signup()
            pid = c.project()
            self.assertEqual(c.solve(pid, provider="local")["status"], "completed")
            earlier = c.call("GET", f"/api/projects/{pid}/analysis").data["completed"]["id"]
            fake.mode = "fail"
            s = c.solve(pid, provider="openai")
            self.assertEqual(s["status"], "failed")
            self.assertEqual(s["mode"], "live")
            self.assertIn("provider", s["error_message"].lower())
            self.assertNotIn("test-key", s["error_message"])
            d = c.call("GET", f"/api/projects/{pid}/analysis").data
            self.assertEqual(d["latest"]["status"], "failed")
            self.assertEqual(d["completed"]["id"], earlier)
            self.assertEqual(d["completed"]["mode"], "demo")
            self.assertEqual(env.app.db.one("SELECT COUNT(*) n FROM artifacts WHERE analysis_id=?", (d["latest"]["id"],))["n"], 0)
            self.assertEqual(c.call("GET", f"/api/projects/{pid}").data["project"]["status"], "needs_attention")
        finally:
            env.close()
            fake.close()


class DatabaseAndSeedTests(unittest.TestCase):
    def test_migrations_are_idempotent_and_foreign_keys_cascade(self):
        env = Env()
        try:
            self.assertEqual(env.app.db.migrate(), 1)
            self.assertEqual(env.app.db.one("SELECT COUNT(*) n FROM schema_version")["n"], 1)
            c = env.client()
            c.signup()
            pid = c.project()
            c.solve(pid)
            uid_ = env.app.db.one("SELECT id FROM users")["id"]
            env.app.db.run("DELETE FROM users WHERE id=?", (uid_,))
            for t in ("projects", "briefs", "analyses", "requirements", "artifacts", "checklist_items", "sessions"):
                self.assertEqual(env.app.db.one(f"SELECT COUNT(*) n FROM {t}")["n"], 0, t)
        finally:
            env.close()

    def test_seed_creates_demo_user_and_two_analysed_projects(self):
        env = Env()
        try:
            from hacksolve.seed import seed
            msg = seed(env.app)
            self.assertIn("demo@hacksolve.local", msg)
            c = env.client()
            pw = msg.split("password ")[1].split()[0]
            self.assertEqual(c.call("POST", "/api/auth/login", {"email": "demo@hacksolve.local", "password": pw}).status, 200)
            ps = c.call("GET", "/api/projects").data["projects"]
            self.assertEqual(sorted(p["status"] for p in ps), ["plan_ready", "plan_ready"])
            self.assertIn("already exists", seed(env.app))
        finally:
            env.close()

    def test_cli_migrate_and_production_seed_guard(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            env = {**os.environ, "DATA_DIR": d, "APP_ENV": "development"}
            r = subprocess.run([sys.executable, "run.py", "migrate"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "Schema version: 1"))
            r = subprocess.run([sys.executable, "run.py", "seed"], cwd=ROOT, env={**env, "APP_ENV": "production"}, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 1)
            self.assertIn("Refusing", r.stdout)


if __name__ == "__main__":
    unittest.main()
