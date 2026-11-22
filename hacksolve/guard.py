"""Code Guard: static checks on AI-generated single-file apps. Nothing here executes the app."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser

MAX_HTML = 250_000
JS_BAN = [(re.compile(p, re.M), msg) for p, msg in (
    (r"\beval\s*\(", "eval()"),
    (r"\bnew\s+Function\b|\bFunction\s*\(\s*['\"`]", "Function constructor"),
    (r"\bset(?:Timeout|Interval)\s*\(\s*['\"`]", "string passed to setTimeout/setInterval"),
    (r"\bfetch\s*\(", "fetch()"),
    (r"XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|RTCPeerConnection", "network API"),
    (r"\bimport\s*\(|^\s*import\s+[\w{*]", "module import"),
    (r"document\s*\.\s*cookie|window\s*\.\s*open\s*\(", "cookie or window access"),
    (r"(?<![\w.$])(?:top|parent)\s*\.|\.\s*(?:top|parent)\s*\.", "frame access"),
)]
HTML_BAN = [(re.compile(p, re.I), msg) for p, msg in (
    (r"<\s*(iframe|object|embed|base|frame)\b", "unsafe tag"),
    (r"http-equiv\s*=\s*[\"']?refresh", "meta refresh"),
    (r"@import|javascript\s*:", "CSS @import or javascript: URL"),
    (r"url\(\s*[\"']?\s*(?:https?:)?//", "external CSS resource"),
    (r"[\u202a-\u202e\u2066-\u2069]", "Trojan Source bidirectional characters"),
)]
URL_ATTRS = {"src", "href", "action", "poster", "data", "formaction", "srcset", "ping", "xlink:href"}


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.handlers, self.external, self._in = [], [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            a = dict(attrs)
            if a.get("src"):
                self.external.append(a["src"])
            self._in = (a.get("type") or "text/javascript").lower()
        for k, v in attrs:
            if v and k.startswith("on"):
                self.handlers.append(v)
            if v and k in URL_ATTRS and re.match(r"\s*(https?:)?//", v, re.I):
                self.external.append(v)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = None

    def handle_data(self, data):
        if self._in is not None and self._in in ("text/javascript", "application/javascript", "module", ""):
            self.scripts.append(data)


def scan_html(html: str) -> list[str]:
    out = []
    if len(html) > MAX_HTML:
        out.append("File is larger than 250 KB")
    if not re.search(r"<!doctype html|<html", html, re.I):
        out.append("Not an HTML document")
    if "</html>" not in html.lower():
        out.append("Output was cut off before </html>")
    for rx, msg in HTML_BAN:
        if rx.search(html):
            out.append(msg)
    c = _Collect()
    try:
        c.feed(html)
        c.close()
    except Exception:
        out.append("HTML could not be parsed")
    if c.external:
        out.append("external resource: " + c.external[0][:80])
    js = "\n".join(c.scripts + c.handlers)
    for rx, msg in JS_BAN:
        if rx.search(js):
            out.append(msg)
    return list(dict.fromkeys(out))


def check_js_syntax(html: str):
    """Returns (status, message); status is ok, fail or skipped. Uses `node --check` when Node exists."""
    node = shutil.which("node")
    if not node:
        return "skipped", "Node.js isn't installed, so JavaScript syntax wasn't checked"
    c = _Collect()
    c.feed(html)
    if not c.scripts:
        return "ok", "No inline scripts to check"
    with tempfile.TemporaryDirectory() as d:
        for n, code in enumerate(c.scripts):
            path = os.path.join(d, f"s{n}.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            try:
                r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=10,
                                   cwd=d, env={"PATH": os.environ.get("PATH", "")}, shell=False)
            except subprocess.TimeoutExpired:
                return "fail", "Syntax check timed out"
            if r.returncode:
                msg = " ".join(l.strip() for l in r.stderr.replace(d, "").splitlines()[:4] if l.strip())
                return "fail", msg[:240]
    return "ok", f"{len(c.scripts)} inline script(s) parse cleanly"


def coverage(html: str) -> dict:
    return {m.group(1).upper(): m.group(2).strip()[:200]
            for m in re.finditer(r"<!--\s*(REQ-\d+)\s*:\s*(.*?)-->", html, re.S | re.I)}
