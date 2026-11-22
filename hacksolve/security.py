"""Password hashing, rate limiting, text sanitising, prompt-injection scanning, secret scrubbing."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import unicodedata
from collections import defaultdict, deque

from .db import now

_N, _R, _P = 2 ** 14, 8, 1
_MAXMEM = 64 * 1024 * 1024
PBKDF2_ITERS = 600_000


def _has_scrypt() -> bool:
    return hasattr(hashlib, "scrypt")     # some Python builds (e.g. LibreSSL) lack it


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    b = lambda x: base64.b64encode(x).decode()
    if _has_scrypt():
        dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=_MAXMEM)
        return f"scrypt${_N}${_R}${_P}${b(salt)}${b(dk)}"
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS, 32)
    return f"pbkdf2${PBKDF2_ITERS}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt" and _has_scrypt():
            _, n, r, p, salt, dk = parts
            calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=_MAXMEM)
        elif parts[0] == "pbkdf2":
            _, iters, salt, dk = parts
            calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters), 32)
        else:
            return False
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


_DUMMY = hash_password("timing-equaliser-not-a-real-password")


def burn_time() -> None:
    """Run a hash so 'unknown email' takes as long as 'wrong password'."""
    verify_password("x", _DUMMY)


COMMON = {"password", "password1", "1234567890", "qwertyuiop", "letmein123", "iloveyou123",
          "admin12345", "welcome123", "hacksolve", "passw0rd123", "password123"}


def password_problems(pw: str, email: str = "", name: str = "") -> list[str]:
    out = []
    if len(pw) < 10:
        out.append("Use at least 10 characters")
    if len(pw) > 128:
        out.append("Use at most 128 characters")
    if sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")) < 2:
        out.append("Mix letters with numbers or symbols")
    low = pw.lower()
    if low in COMMON or low.rstrip("0123456789") in COMMON:
        out.append("That password is too common")
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in low:
        out.append("Don't include your email name")
    return out


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Sliding-window limiter, in memory. Fine for one process; see docs/DEPLOYMENT.md."""

    def __init__(self):
        self.hits, self.lock = defaultdict(deque), threading.Lock()

    def check(self, key: str, limit: int, window: int = 60):
        t = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and t - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False, int(window - (t - q[0])) + 1
            q.append(t)
            if len(self.hits) > 20000:
                for k in [k for k, v in self.hits.items() if not v]:
                    del self.hits[k]
            return True, 0


INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\u00ad]")
CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(s: str, max_len: int = 60000) -> str:
    s = unicodedata.normalize("NFC", s).replace("\r\n", "\n").replace("\r", "\n")
    return CTRL.sub("", INVISIBLE.sub("", s))[:max_len]


def count_hidden(s: str) -> int:
    return len(INVISIBLE.findall(s))


INJECTION = [re.compile(p, re.I) for p in (
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)",
    r"disregard\s+(all\s+)?(previous|prior)\s+(instructions|rules)",
    r"reveal\s+(your\s+)?(system\s+prompt|api[\s_-]?keys?|secrets?|\.?env)",
    r"\bexfiltrat",
    r"curl\s+[^\n]{0,200}?\|\s*(ba)?sh",
    r"<\s*/?\s*(system|assistant|tool_call|instructions)\s*>",
    r"you\s+are\s+now\s+(in\s+)?(developer|dan|jailbreak)",
    r"send\s+[^\n]{0,200}?(api[\s_-]?keys?|\.env|passwords?)",
)]


def split_injection(text: str):
    """Return (text without instruction-like lines, the flagged lines)."""
    keep, flagged = [], []
    for ln in text.split("\n"):
        (flagged if any(p.search(ln) for p in INJECTION) else keep).append(ln)
    return "\n".join(keep), [f.strip()[:200] for f in flagged]


SECRETS = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_\-]{16,}", r"AIza[0-9A-Za-z_\-]{20,}", r"ghp_[A-Za-z0-9]{20,}",
    r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}", r"(?i)(x-api-key|x-goog-api-key|api[_-]?key)[\"'\s:=]+[A-Za-z0-9._\-]{12,}")]


def scrub(s: str) -> str:
    for p in SECRETS:
        s = p.sub("[redacted]", s)
    return s


RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str):
    """Display-safe basename and lowercase extension. Never used to build a storage path."""
    name = unicodedata.normalize("NFKC", name or "").replace("\x00", "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ]", "_", name).strip(" .")[:100]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.lower() in RESERVED:
        stem = "_" + stem
    return (f"{stem}.{ext}" if ext else stem) or "upload", ("." + ext.lower() if ext else "")


def log_event(db, kind: str, detail: str, user_id=None, ip=None) -> None:
    db.run("INSERT INTO security_events(user_id,kind,detail,ip,created_at) VALUES(?,?,?,?,?)",
           (user_id, kind, scrub(detail)[:500], ip, now()))
