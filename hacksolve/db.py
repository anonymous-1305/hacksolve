"""SQLite persistence: per-thread connections, WAL, foreign keys, versioned migrations."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid() -> str:
    return uuid.uuid4().hex


SCHEMA_V1 = """
CREATE TABLE users(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL, avatar TEXT, preferences TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE sessions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, ip TEXT);
CREATE TABLE projects(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
  deadline TEXT, competition_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_projects_user ON projects(user_id);
CREATE TABLE file_uploads(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, mime_type TEXT NOT NULL, size INTEGER NOT NULL,
  storage_path TEXT NOT NULL, checksum TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE briefs(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  original_text TEXT NOT NULL, source_type TEXT NOT NULL,
  file_upload_id TEXT REFERENCES file_uploads(id) ON DELETE SET NULL,
  extracted_content TEXT NOT NULL, injection_flags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE INDEX idx_briefs_project ON briefs(project_id);
CREATE TABLE analyses(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  status TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'demo', model_used TEXT, confidence REAL,
  stage TEXT, progress INTEGER NOT NULL DEFAULT 0, stages_json TEXT NOT NULL DEFAULT '[]',
  result_json TEXT, explanation TEXT, explanation_version INTEGER NOT NULL DEFAULT 1,
  implementation_prompt TEXT, error_message TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, completed_at TEXT);
CREATE INDEX idx_analyses_project ON analyses(project_id);
CREATE TABLE requirements(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  analysis_id TEXT REFERENCES analyses(id) ON DELETE CASCADE, ref TEXT NOT NULL,
  category TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT 'must', status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE artifacts(
  id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, content TEXT NOT NULL, checks_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE checklist_items(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'todo',
  due_date TEXT, position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE security_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, kind TEXT NOT NULL,
  detail TEXT NOT NULL, ip TEXT, created_at TEXT NOT NULL);
"""
MIGRATIONS = [SCHEMA_V1]


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            self._local.c = c
        return c

    def all(self, sql, args=()):
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def run(self, sql, args=()) -> int:
        c = self.conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.rowcount

    def migrate(self) -> int:
        c = self.conn()
        c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
        current = c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            c.executescript(sql)
            c.execute("INSERT INTO schema_version(version) VALUES(?)", (i,))
            c.commit()
        return len(MIGRATIONS)
