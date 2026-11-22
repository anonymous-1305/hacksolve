"""HTTP server: JSON API, static files, strict security headers. Standard library only."""
from __future__ import annotations

import hashlib
import http.cookies
import json
import logging
import os
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, ai, auth, parsers, projects as P
from .analysis import AnalysisService
from .config import Config
from .db import Database, now, uid
from .security import RateLimiter, log_event
from .util import ApiError, Fields

log = logging.getLogger("hacksolve.http")
ID = r"(?P<id>[0-9a-f]{32})"
SEC = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
       "Cross-Origin-Opener-Policy": "same-origin", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
CSP_APP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
           "font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; "
           "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
CSP_PREVIEW = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
PROBE = ('<script>onerror=function(m,s,l){parent.postMessage({hsErr:m+" (line "+l+")"},"*")};'
         'addEventListener("unhandledrejection",function(e){parent.postMessage({hsErr:"Unhandled: "+e.reason},"*")});'
         'addEventListener("load",function(){setTimeout(function(){parent.postMessage({hsLen:document.body?document.body.innerText.length:0},"*")},400)});</script>')
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json"}
REJECT_KINDS = {"bad_file", "unsafe_archive", "archive_limits", "macros", "unsafe_xml", "unsupported_type", "encrypted", "timeout"}


def ok(data, status=200, headers=None):
    return status, {"Content-Type": "application/json; charset=utf-8", **(headers or {})}, json.dumps({"data": data}).encode()


class Request:
    def __init__(self, method, path, query, headers, body, ip):
        self.method, self.path, self.query, self.headers, self.body, self.ip = method, path, query, headers, body, ip

    def json(self, empty_ok=False):
        if not self.body and empty_ok:
            return {}
        if self.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise ApiError(415, "unsupported_media_type", "Send JSON with Content-Type: application/json")
        if len(self.body) > 256 * 1024:
            raise ApiError(413, "payload_too_large", "Request body is too large")
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ApiError(400, "bad_json", "Request body isn't valid JSON")

    def token(self):
        c = http.cookies.SimpleCookie()
        try:
            c.load(self.headers.get("cookie", ""))
        except http.cookies.CookieError:
            return None
        return c["hs_session"].value if "hs_session" in c else None


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Database(cfg.db_path)
        self.db.migrate()
        cfg.upload_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = RateLimiter()
        self.analysis = AnalysisService(self.db, cfg)
        R = [("GET", "/api/health", self.health, False, None), ("GET", "/api/providers", self.providers, True, None),
             ("POST", "/api/auth/register", self.register, False, "auth"), ("POST", "/api/auth/login", self.login, False, "auth"),
             ("POST", "/api/auth/logout", self.logout, False, None), ("GET", "/api/auth/me", self.me, True, None),
             ("PATCH", "/api/auth/me", self.patch_me, True, None),
             ("GET", "/api/projects", self.list_projects, True, None), ("POST", "/api/projects", self.create_project, True, None),
             ("GET", f"/api/projects/{ID}", self.get_project, True, None), ("PATCH", f"/api/projects/{ID}", self.patch_project, True, None),
             ("DELETE", f"/api/projects/{ID}", self.delete_project, True, None),
             ("POST", f"/api/projects/{ID}/brief", self.post_brief, True, None),
             ("POST", f"/api/projects/{ID}/files", self.post_file, True, "upload"),
             ("POST", f"/api/projects/{ID}/analyze", self.analyze, True, "analyze"),
             ("POST", f"/api/projects/{ID}/analysis/cancel", self.cancel, True, None),
             ("GET", f"/api/projects/{ID}/analysis", self.get_analysis, True, None),
             ("GET", f"/api/projects/{ID}/analysis/status", self.analysis_status, True, None),
             ("GET", f"/api/projects/{ID}/preview", self.preview, True, None),
             ("GET", f"/api/projects/{ID}/checklist", self.get_checklist, True, None),
             ("POST", f"/api/projects/{ID}/checklist", self.add_check, True, None),
             ("GET", f"/api/projects/{ID}/export", self.export, True, None),
             ("DELETE", f"/api/projects/{ID}/artifacts", self.delete_artifacts, True, None),
             ("PATCH", f"/api/analysis/{ID}", self.patch_analysis, True, None),
             ("POST", f"/api/analysis/{ID}/regenerate", self.regenerate, True, "analyze"),
             ("PATCH", f"/api/checklist/{ID}", self.patch_check, True, None)]
        self.routes = [(m, re.compile("^" + p + "$"), fn, a, lim) for m, p, fn, a, lim in R]

    # ------------------------------------------------------------ dispatch
    def handle(self, req: Request):
        t0, user = time.monotonic(), None
        try:
            if req.method == "OPTIONS":
                resp = 204, self._cors(req), b""
            else:
                resp = self._dispatch(req)
                user = getattr(req, "user", None)
        except ApiError as e:
            resp = self._err(e)
        except Exception:
            log.exception("unhandled error")
            resp = self._err(ApiError(500, "internal_error", "Something went wrong on our side"))
        status, headers, body = resp
        headers = {**SEC, **headers}
        if req.path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        if self.cfg.cookie_secure:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        headers.setdefault("Content-Security-Policy", CSP_APP)
        log.info("%s %s %s %dms user=%s", req.method, req.path, status, (time.monotonic() - t0) * 1000, (user or {}).get("id", "-")[:8])
        return status, headers, body

    def _dispatch(self, req):
        if req.method == "GET" and not req.path.startswith("/api/"):
            return self.static(req)
        route, params = None, {}
        for m, rx, fn, a, lim in self.routes:
            mt = rx.match(req.path)
            if mt and m == req.method:
                route, params = (fn, a, lim), mt.groupdict()
                break
        if not route:
            raise ApiError(404, "not_found", "Not found")
        fn, needs_auth, limit = route
        if req.method != "GET":
            self._csrf(req)
        req.user = auth.user_from_token(self.db, req.token())
        if needs_auth and not req.user:
            raise ApiError(401, "unauthorized", "Please log in")
        if limit:
            n = {"auth": self.cfg.rate_auth, "analyze": self.cfg.rate_analyze, "upload": self.cfg.rate_upload}[limit]
            allowed, wait = self.limiter.check(f"{limit}:{req.user['id'] if req.user else req.ip}", n)
            if not allowed:
                log_event(self.db, "rate_limited", f"{limit} limit hit on {req.path[:60]}", (req.user or {}).get("id"), req.ip)
                raise ApiError(429, "rate_limited", "Too many requests. Slow down and try again shortly.", headers={"Retry-After": str(wait)})
        return fn(req, req.user, **params)

    def _csrf(self, req):
        if req.headers.get("x-requested-with") != "HackSolve":
            raise ApiError(403, "csrf_blocked", "Missing anti-forgery header")
        origin = req.headers.get("origin")
        if origin and origin.rstrip("/") not in self.cfg.allowed_origins and urllib.parse.urlparse(origin).netloc != req.headers.get("host", ""):
            log_event(self.db, "csrf_blocked", f"Cross-origin request from {origin[:100]}", None, req.ip)
            raise ApiError(403, "csrf_blocked", "Cross-origin request blocked")

    def _cors(self, req):
        o = (req.headers.get("origin") or "").rstrip("/")
        if o and o in self.cfg.allowed_origins:
            return {"Access-Control-Allow-Origin": o, "Access-Control-Allow-Credentials": "true", "Vary": "Origin",
                    "Access-Control-Allow-Headers": "Content-Type, X-Requested-With, X-Filename",
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE"}
        return {}

    def _err(self, e: ApiError):
        body = {"error": {"code": e.code, "message": e.message, **({"details": e.details} if e.details else {})}}
        return e.status, {"Content-Type": "application/json; charset=utf-8", **e.headers}, json.dumps(body).encode()

    def _cookie(self, token, max_age):
        parts = [f"hs_session={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
        return "; ".join(parts + (["Secure"] if self.cfg.cookie_secure else []))

    def static(self, req):
        rel = "index.html" if req.path in ("/", "") else req.path.lstrip("/")
        root = self.cfg.web_dir.resolve()
        fp = (root / rel).resolve()
        if root not in fp.parents or not fp.is_file() or fp.suffix not in TYPES:
            raise ApiError(404, "not_found", "Not found")
        data = fp.read_bytes()
        etag = '"' + hashlib.sha1(data).hexdigest()[:16] + '"'
        if req.headers.get("if-none-match") == etag:
            return 304, {"ETag": etag, "Cache-Control": "no-cache"}, b""
        return 200, {"Content-Type": TYPES[fp.suffix], "Cache-Control": "no-cache", "ETag": etag}, data

    # ------------------------------------------------------------ handlers
    def health(self, req, u):
        return ok({"status": "ok", "version": __version__, "time": now()})

    def providers(self, req, u):
        try:
            default = ai.pick(self.cfg, "auto").id
        except ai.AIError:      # AI_PROVIDER names a provider that has no key: report it instead of crashing
            default = None
        return ok({"providers": ai.status(self.cfg), "default": default})

    def register(self, req, u):
        f = Fields(req.json())
        name, email, pw = f.str("name", 1, 80), f.email(), f.str("password", 1, 200)
        f.done()
        user, token = auth.register(self.db, self.cfg, name, email, pw, req.ip)
        return ok({"user": user}, 201, {"Set-Cookie": self._cookie(token, self.cfg.session_days * 86400)})

    def login(self, req, u):
        f = Fields(req.json())
        email, pw = f.email(), f.str("password", 1, 200)
        f.done()
        user, token = auth.login(self.db, self.cfg, email, pw, req.ip)
        return ok({"user": user}, 200, {"Set-Cookie": self._cookie(token, self.cfg.session_days * 86400)})

    def logout(self, req, u):
        auth.logout(self.db, req.token())
        return 204, {"Set-Cookie": self._cookie("", 0)}, b""

    def me(self, req, u):
        return ok({"user": auth.public_user(u)})

    def patch_me(self, req, u):
        f = Fields(req.json())
        name = f.str("name", 1, 80, required=False)
        prefs = req.json().get("preferences")
        if prefs is not None and (not isinstance(prefs, dict) or len(json.dumps(prefs)) > 2000):
            f.errors["preferences"] = "Must be a small object"
        f.done()
        if name:
            self.db.run("UPDATE users SET name=?, updated_at=? WHERE id=?", (name, now(), u["id"]))
        if prefs is not None:
            self.db.run("UPDATE users SET preferences=?, updated_at=? WHERE id=?", (json.dumps(prefs), now(), u["id"]))
        return ok({"user": auth.public_user(self.db.one("SELECT * FROM users WHERE id=?", (u["id"],)))})

    def list_projects(self, req, u):
        return ok({"projects": P.list_projects(self.db, u["id"])})

    def create_project(self, req, u):
        f = Fields(req.json())
        title, desc = f.str("title", 1, 120), f.str("description", 0, 1000, required=False, default="")
        deadline, url = f.date("deadline"), f.url("competition_url")
        f.done()
        p = P.create(self.db, u["id"], title, desc, deadline, url)
        return ok({"project": P.card(self.db, p)}, 201)

    def get_project(self, req, u, id):
        p = P.own(self.db, u["id"], id)
        a = P.latest_analysis(self.db, id)
        return ok({"project": P.card(self.db, p), "brief": P.brief_view(P.latest_brief(self.db, id)),
                   "requirements": self.db.all("SELECT id,ref,category,title,priority,status FROM requirements WHERE project_id=? ORDER BY rowid", (id,)),
                   "checklist": P.checklist(self.db, id), "analysis": self.analysis.summary(a["id"]) if a else None,
                   "files": self.db.all("SELECT id,filename,mime_type,size,created_at FROM file_uploads WHERE project_id=?", (id,))})

    def patch_project(self, req, u, id):
        p = P.own(self.db, u["id"], id)
        f = Fields(req.json())
        ch = {}
        if f.has("title"):
            ch["title"] = f.str("title", 1, 120)
        if f.has("description"):
            ch["description"] = f.str("description", 0, 1000, required=False, default="")
        if f.has("deadline"):
            ch["deadline"] = f.date("deadline")
        if f.has("competition_url"):
            ch["competition_url"] = f.url("competition_url")
        f.done()
        return ok({"project": P.card(self.db, P.update(self.db, p, ch))})

    def delete_project(self, req, u, id):
        P.delete(self.db, self.cfg, P.own(self.db, u["id"], id))
        return 204, {}, b""

    def post_brief(self, req, u, id):
        p = P.own(self.db, u["id"], id)
        f = Fields(req.json())
        text = f.str("text", 1, 60000)
        f.done()
        return ok({"brief": P.set_brief(self.db, p, text, "paste", None, (u["id"], req.ip))}, 201)

    def post_file(self, req, u, id):
        p = P.own(self.db, u["id"], id)
        if req.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
            raise ApiError(415, "unsupported_media_type", "Send the raw file with Content-Type: application/octet-stream")
        name = urllib.parse.unquote(req.headers.get("x-filename", ""))[:300]
        try:
            res = parsers.parse_upload(name, req.body, self.cfg.max_upload_bytes)
        except parsers.ParseError as e:
            if e.code in REJECT_KINDS or e.code == "too_large":
                log_event(self.db, "upload_rejected", f"{e.code}: {name[:80]}", u["id"], req.ip)
            raise ApiError({"unsupported_type": 415, "too_large": 413}.get(e.code, 422), e.code, e.message)
        d = self.cfg.upload_dir / id
        d.mkdir(parents=True, exist_ok=True)
        fid = uid()
        path = d / (fid + res["ext"])
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)   # O_BINARY: no newline rewriting on Windows
        with os.fdopen(fd, "wb") as fh:
            fh.write(req.body)
        self.db.run("INSERT INTO file_uploads(id,user_id,project_id,filename,mime_type,size,storage_path,checksum,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (fid, u["id"], id, res["name"], res["mime"], len(req.body), str(path), hashlib.sha256(req.body).hexdigest(), now()))
        brief = P.set_brief(self.db, p, res["text"], "file", fid, (u["id"], req.ip))
        return ok({"file": {"id": fid, "filename": res["name"], "size": len(req.body), "truncated": res["truncated"], "warnings": res["warnings"]}, "brief": brief}, 201)

    def analyze(self, req, u, id):
        P.own(self.db, u["id"], id)
        f = Fields(req.json(empty_ok=True))
        prov = f.enum("provider", ("auto", "local", "anthropic", "openai", "gemini"), default="auto")
        f.done()
        if not P.latest_brief(self.db, id):
            raise ApiError(409, "no_brief", "Add a brief before analyzing")
        return ok({"analysis": self.analysis.start(id, prov)}, 202)

    def cancel(self, req, u, id):
        P.own(self.db, u["id"], id)
        self.analysis.cancel(id)
        return ok({"cancelling": True})

    def analysis_status(self, req, u, id):
        P.own(self.db, u["id"], id)
        a = P.latest_analysis(self.db, id)
        return ok({"analysis": self.analysis.summary(a["id"]) if a else None})

    def get_analysis(self, req, u, id):
        P.own(self.db, u["id"], id)
        latest, done = P.latest_analysis(self.db, id), P.latest_analysis(self.db, id, completed=True)
        out = None
        if done:
            art = self.db.one("SELECT content, checks_json FROM artifacts WHERE analysis_id=?", (done["id"],))
            out = {**self.analysis.summary(done["id"]), "result": json.loads(done["result_json"] or "null"), "explanation": done["explanation"],
                   "explanation_version": done["explanation_version"], "implementation_prompt": done["implementation_prompt"],
                   "artifact": {"content": art["content"], "checks": json.loads(art["checks_json"])} if art else None}
        return ok({"latest": self.analysis.summary(latest["id"]) if latest else None, "completed": out,
                   "requirements": self.db.all("SELECT id,ref,category,title,priority,status FROM requirements WHERE project_id=? ORDER BY rowid", (id,))})

    def preview(self, req, u, id):
        P.own(self.db, u["id"], id)
        done = P.latest_analysis(self.db, id, completed=True)
        art = self.db.one("SELECT content, checks_json FROM artifacts WHERE analysis_id=?", (done["id"],)) if done else None
        if not art:
            raise ApiError(404, "not_found", "No generated app yet")
        checks = json.loads(art["checks_json"])
        html = art["content"]
        if checks.get("guard"):
            html = "<!doctype html><meta charset=utf-8><body style='font:16px system-ui;padding:24px'><b>Blocked by Code Guard.</b><p>The generated code failed the safety scan, so it isn't run. Read it in the Code tab.</p>"
        elif "probe=1" in (req.query or ""):
            html = re.sub(r"<head[^>]*>", lambda m: m.group(0) + PROBE, html, count=1, flags=re.I) if re.search(r"<head[^>]*>", html, re.I) else PROBE + html
        return 200, {"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": CSP_PREVIEW, "X-Frame-Options": "SAMEORIGIN"}, html.encode()

    def get_checklist(self, req, u, id):
        P.own(self.db, u["id"], id)
        return ok({"checklist": P.checklist(self.db, id)})

    def add_check(self, req, u, id):
        P.own(self.db, u["id"], id)
        f = Fields(req.json())
        title = f.str("title", 1, 200)
        f.done()
        item = P.add_item(self.db, id, title)
        P.refresh_status(self.db, id)
        return ok({"item": item}, 201)

    def patch_check(self, req, u, id):
        f = Fields(req.json())
        ch = {}
        if f.has("status"):
            ch["status"] = f.enum("status", ("todo", "done"), required=True)
        if f.has("title"):
            ch["title"] = f.str("title", 1, 200)
        if f.has("due_date"):
            ch["due_date"] = f.date("due_date")
        f.done()
        return ok({"item": P.patch_item(self.db, u["id"], id, ch)})

    def patch_analysis(self, req, u, id):
        a = self.db.one("SELECT a.* FROM analyses a JOIN projects p ON p.id=a.project_id WHERE a.id=? AND p.user_id=?", (id, u["id"]))
        if not a:
            raise ApiError(404, "not_found", "Analysis not found")
        body = req.json()
        f = Fields(body)
        expl = f.str("explanation", 0, 4000, required=False)
        reqs = body.get("requirements")
        if reqs is not None and (not isinstance(reqs, list) or len(reqs) > 20 or not all(isinstance(r, dict) and r.get("status") in ("open", "done", "blocked") and isinstance(r.get("id"), str) for r in reqs)):
            f.errors["requirements"] = "Expected a list of {id, status: open|done|blocked}"
        f.done()
        if expl is not None:
            self.db.run("UPDATE analyses SET explanation=?, explanation_version=explanation_version+1 WHERE id=?", (expl, id))
        for r in reqs or []:
            self.db.run("UPDATE requirements SET status=? WHERE id=? AND project_id=?", (r["status"], r["id"], a["project_id"]))
        a = self.db.one("SELECT explanation, explanation_version FROM analyses WHERE id=?", (id,))
        return ok({"explanation": a["explanation"], "explanation_version": a["explanation_version"]})

    def regenerate(self, req, u, id):
        a = self.db.one("SELECT a.project_id FROM analyses a JOIN projects p ON p.id=a.project_id WHERE a.id=? AND p.user_id=?", (id, u["id"]))
        if not a:
            raise ApiError(404, "not_found", "Analysis not found")
        f = Fields(req.json(empty_ok=True))
        prov = f.enum("provider", ("auto", "local", "anthropic", "openai", "gemini"), default="auto")
        f.done()
        return ok({"analysis": self.analysis.start(a["project_id"], prov)}, 202)

    def export(self, req, u, id):
        p = P.own(self.db, u["id"], id)
        slug = re.sub(r"[^a-z0-9]+", "-", p["title"].lower()).strip("-")[:40] or "project"
        return 200, {"Content-Type": "application/json; charset=utf-8", "Content-Disposition": f'attachment; filename="hacksolve-{slug}.json"'}, \
            json.dumps({"data": P.export(self.db, p)}, indent=1).encode()

    def delete_artifacts(self, req, u, id):
        P.delete_artifacts(self.db, P.own(self.db, u["id"], id))
        return 204, {}, b""


class Handler(BaseHTTPRequestHandler):
    server_version, sys_version, protocol_version, timeout = "HackSolve", "", "HTTP/1.1", 30

    def log_message(self, *a):
        pass

    def _serve(self):
        app: App = self.server.app
        body = b""
        parsed = urllib.parse.urlparse(self.path)
        try:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                raise ApiError(411, "length_required", "Chunked bodies are not supported")
            n = int(self.headers.get("Content-Length") or 0)       # always consume the body, or keep-alive requests desync
            if n < 0 or n > app.cfg.max_upload_bytes + 65536:
                raise ApiError(413, "payload_too_large", "Request body is too large")
            body = self.rfile.read(n) if n else b""
        except (ValueError, ApiError) as e:
            e = e if isinstance(e, ApiError) else ApiError(400, "bad_request", "Invalid Content-Length")
            status, headers, payload = app._err(e)
            self.close_connection = True
            return self._send(status, {**SEC, **headers, "Connection": "close"}, payload)
        ip = self.client_address[0]
        if app.cfg.trust_proxy and self.headers.get("X-Forwarded-For"):
            ip = self.headers["X-Forwarded-For"].split(",")[0].strip()
        req = Request("GET" if self.command == "HEAD" else self.command, parsed.path, parsed.query, {k.lower(): v for k, v in self.headers.items()}, body, ip)
        self._send(*app.handle(req))

    def _send(self, status, headers, body):
        try:
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_HEAD = do_POST = do_PATCH = do_DELETE = do_OPTIONS = _serve


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, app: App):
        super().__init__(addr, Handler)
        self.app = app


def make_server(cfg: Config) -> Server:
    """Bind cfg.port, or the next free port (up to 20 higher) when it is already taken."""
    app, last = App(cfg), None
    for port in ([0] if cfg.port == 0 else range(cfg.port, cfg.port + 20)):
        try:
            return Server((cfg.host, port), app)
        except OSError as e:
            last = e
    app.analysis.pool.shutdown(wait=False)
    raise OSError(f"Ports {cfg.port}-{cfg.port + 19} are all busy. Set PORT in .env to a free port.") from last
