"""Projects, briefs, checklist, export and deletion. Every query is scoped to the owning user."""
from __future__ import annotations

import json
import shutil
from collections import Counter

from .db import now, uid
from .security import clean_text, log_event, split_injection
from .util import ApiError

STATUSES = ("draft", "analyzing", "plan_ready", "needs_attention", "ready_to_build")
DEFAULT_CHECKS = ["Prototype link works in a private window", "Repository is public with a README",
                  "Pitch deck or video is uploaded", "Demo rehearsed twice", "Official rules re-read for eligibility"]


def own(db, user_id: str, pid: str) -> dict:
    p = db.one("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, user_id))
    if not p:
        raise ApiError(404, "not_found", "Project not found")
    return p


def latest_analysis(db, pid: str, completed: bool = False):
    q = "SELECT * FROM analyses WHERE project_id=?" + (" AND status='completed'" if completed else "") + " ORDER BY rowid DESC LIMIT 1"
    return db.one(q, (pid,))


def latest_brief(db, pid: str):
    return db.one("SELECT * FROM briefs WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (pid,))


def checklist(db, pid: str):
    return db.all("SELECT * FROM checklist_items WHERE project_id=? ORDER BY position, rowid", (pid,))


def refresh_status(db, pid: str) -> str:
    a = latest_analysis(db, pid)
    if not a:
        status = "draft"
    elif a["status"] in ("queued", "running"):
        status = "analyzing"
    elif a["status"] == "failed":
        status = "needs_attention"
    else:
        done = latest_analysis(db, pid, completed=True)
        if not done:
            status = "draft"
        else:
            ok = json.loads(done["result_json"] or "{}").get("checks", {}).get("ok", False)
            items = checklist(db, pid)
            status = "needs_attention" if not ok else "ready_to_build" if items and all(i["status"] == "done" for i in items) else "plan_ready"
    db.run("UPDATE projects SET status=?, updated_at=? WHERE id=?", (status, now(), pid))
    return status


def card(db, p: dict) -> dict:
    items = checklist(db, p["id"])
    done = sum(i["status"] == "done" for i in items)
    a = latest_analysis(db, p["id"])
    cats = Counter(r["category"] for r in db.all("SELECT category FROM requirements WHERE project_id=?", (p["id"],)))
    completed = latest_analysis(db, p["id"], completed=True)
    progress = (10 if latest_brief(db, p["id"]) else 0) + (40 if completed else 0) + (50 * done // len(items) if items else 0)
    return {**{k: p[k] for k in ("id", "title", "description", "status", "deadline", "competition_url", "created_at", "updated_at")},
            "progress": progress, "tags": [c for c, _ in cats.most_common(3)], "checklist": {"done": done, "total": len(items)},
            "analysis": {"id": a["id"], "status": a["status"], "stage": a["stage"], "progress": a["progress"], "mode": a["mode"]} if a else None}


def list_projects(db, user_id: str):
    return [card(db, p) for p in db.all("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC, rowid DESC", (user_id,))]


def create(db, user_id: str, title, description, deadline, url) -> dict:
    pid, t = uid(), now()
    db.run("INSERT INTO projects(id,user_id,title,description,deadline,competition_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
           (pid, user_id, title, description or "", deadline, url, t, t))
    return db.one("SELECT * FROM projects WHERE id=?", (pid,))


def update(db, p: dict, changes: dict) -> dict:
    cols = [k for k in ("title", "description", "deadline", "competition_url") if k in changes]
    if cols:
        db.run(f"UPDATE projects SET {', '.join(c + '=?' for c in cols)}, updated_at=? WHERE id=?",
               [changes[c] for c in cols] + [now(), p["id"]])
    return db.one("SELECT * FROM projects WHERE id=?", (p["id"],))


def set_brief(db, p: dict, text: str, source: str, upload_id=None, actor=(None, None)) -> dict:
    text = clean_text(text)
    if len(text.strip()) < 20:
        raise ApiError(422, "validation_error", "The brief is too short", {"text": "Add at least 20 characters"})
    _, flagged = split_injection(text)
    if flagged:
        log_event(db, "prompt_injection", f"{len(flagged)} instruction-like line(s) in a brief: {flagged[0][:120]}", *actor)
    bid = uid()
    db.run("INSERT INTO briefs(id,project_id,original_text,source_type,file_upload_id,extracted_content,injection_flags,created_at) VALUES(?,?,?,?,?,?,?,?)",
           (bid, p["id"], text, source, upload_id, text, json.dumps(flagged), now()))
    db.run("UPDATE projects SET updated_at=? WHERE id=?", (now(), p["id"]))
    return brief_view(db.one("SELECT * FROM briefs WHERE id=?", (bid,)))


def brief_view(b):
    if not b:
        return None
    return {"id": b["id"], "text": b["extracted_content"], "source_type": b["source_type"], "file_upload_id": b["file_upload_id"],
            "injection_flags": json.loads(b["injection_flags"] or "[]"), "chars": len(b["extracted_content"]), "created_at": b["created_at"]}


def seed_checklist(db, pid: str, deliverables) -> None:
    if checklist(db, pid):
        return
    items = [(d[:200], "From the brief") for d in deliverables[:4]] + [(c, "Suggested by HackSolve") for c in DEFAULT_CHECKS]
    for i, (title, desc) in enumerate(items):
        db.run("INSERT INTO checklist_items(id,project_id,title,description,position) VALUES(?,?,?,?,?)", (uid(), pid, title, desc, i))


def add_item(db, pid: str, title: str) -> dict:
    n = len(checklist(db, pid))
    iid = uid()
    db.run("INSERT INTO checklist_items(id,project_id,title,description,position) VALUES(?,?,?,?,?)", (iid, pid, title, "Added by you", n))
    return db.one("SELECT * FROM checklist_items WHERE id=?", (iid,))


def patch_item(db, user_id: str, iid: str, changes: dict) -> dict:
    row = db.one("SELECT c.* FROM checklist_items c JOIN projects p ON p.id=c.project_id WHERE c.id=? AND p.user_id=?", (iid, user_id))
    if not row:
        raise ApiError(404, "not_found", "Checklist item not found")
    cols = [k for k in ("status", "title", "due_date") if k in changes]
    if cols:
        db.run(f"UPDATE checklist_items SET {', '.join(c + '=?' for c in cols)} WHERE id=?", [changes[c] for c in cols] + [iid])
        refresh_status(db, row["project_id"])
    return db.one("SELECT * FROM checklist_items WHERE id=?", (iid,))


def export(db, p: dict) -> dict:
    analyses = db.all("SELECT * FROM analyses WHERE project_id=? ORDER BY rowid", (p["id"],))
    for a in analyses:
        a["result"] = json.loads(a.pop("result_json") or "null")
        a["stages"] = json.loads(a.pop("stages_json") or "[]")
    files = db.all("SELECT id,filename,mime_type,size,checksum,created_at FROM file_uploads WHERE project_id=?", (p["id"],))
    return {"exported_at": now(), "project": {k: p[k] for k in ("id", "title", "description", "status", "deadline", "competition_url", "created_at")},
            "briefs": [brief_view(b) for b in db.all("SELECT * FROM briefs WHERE project_id=? ORDER BY rowid", (p["id"],))],
            "requirements": db.all("SELECT ref,category,title,priority,status FROM requirements WHERE project_id=?", (p["id"],)),
            "analyses": analyses, "artifacts": db.all("SELECT analysis_id,kind,content,created_at FROM artifacts WHERE project_id=?", (p["id"],)),
            "checklist": [{k: i[k] for k in ("title", "description", "status", "due_date")} for i in checklist(db, p["id"])], "files": files}


def delete_artifacts(db, p: dict) -> None:
    db.run("DELETE FROM analyses WHERE project_id=?", (p["id"],))
    db.run("DELETE FROM requirements WHERE project_id=?", (p["id"],))
    refresh_status(db, p["id"])


def delete(db, cfg, p: dict) -> None:
    db.run("DELETE FROM projects WHERE id=?", (p["id"],))
    shutil.rmtree(cfg.upload_dir / p["id"], ignore_errors=True)
