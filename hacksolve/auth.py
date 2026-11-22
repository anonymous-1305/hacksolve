"""Accounts and sessions. Session tokens are random; only their SHA-256 is stored."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .db import now, uid
from .security import (burn_time, hash_password, log_event, new_token, password_problems,
                       token_hash, verify_password)
from .util import ApiError


def public_user(u: dict) -> dict:
    return {"id": u["id"], "name": u["name"], "email": u["email"], "avatar": u["avatar"],
            "preferences": json.loads(u["preferences"] or "{}"), "created_at": u["created_at"]}


def _session(db, cfg, user_id: str, ip) -> str:
    token = new_token()
    exp = (datetime.now(timezone.utc) + timedelta(days=cfg.session_days)).isoformat(timespec="seconds")
    db.run("INSERT INTO sessions(id,user_id,token_hash,created_at,expires_at,ip) VALUES(?,?,?,?,?,?)",
           (uid(), user_id, token_hash(token), now(), exp, ip))
    db.run("DELETE FROM sessions WHERE expires_at < ?", (now(),))
    return token


def register(db, cfg, name: str, email: str, password: str, ip):
    problems = password_problems(password, email, name)
    if problems:
        raise ApiError(422, "validation_error", "Some fields need attention", {"password": "; ".join(problems)})
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        raise ApiError(409, "email_taken", "An account with this email already exists")
    user_id, t = uid(), now()
    db.run("INSERT INTO users(id,name,email,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)",
           (user_id, name, email, hash_password(password), t, t))
    return public_user(db.one("SELECT * FROM users WHERE id=?", (user_id,))), _session(db, cfg, user_id, ip)


def login(db, cfg, email: str, password: str, ip):
    u = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not u:
        burn_time()
    if not u or not verify_password(password, u["password_hash"]):
        log_event(db, "login_failed", "Failed login attempt", u["id"] if u else None, ip)
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
    return public_user(u), _session(db, cfg, u["id"], ip)


def user_from_token(db, token):
    if not token:
        return None
    return db.one("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                  "WHERE s.token_hash=? AND s.expires_at > ?", (token_hash(token), now()))


def logout(db, token) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
