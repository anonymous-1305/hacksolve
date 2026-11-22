"""Background analysis pipeline: read, extract, build, Code Guard, syntax check, repair, package."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from . import ai, guard, projects
from .db import now, uid
from .security import scrub, split_injection
from .util import ApiError

log = logging.getLogger("hacksolve.analysis")
STAGES = [("read", "Read the brief"), ("extract", "Extract requirements and plan"), ("build", "Build the app"),
          ("guard", "Code Guard scan"), ("syntax", "Syntax check"), ("repair", "Repair if needed"), ("package", "Package and save")]
FRIENDLY = {"not_configured": "That AI provider isn't configured on this server.", "auth": "The AI provider rejected the server's key.",
            "rate_limited": "The AI provider's rate or usage limit was reached. Try again later.", "timeout": "The AI provider took too long to answer.",
            "network": "The server couldn't reach the AI provider.", "upstream": "The AI provider had a problem. Try again.",
            "bad_response": "The AI provider's answer couldn't be used."}


class Cancelled(Exception):
    pass


class AnalysisError(Exception):
    pass


class AnalysisService:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg
        self.pool = ThreadPoolExecutor(max_workers=cfg.workers, thread_name_prefix="analysis")
        db.run("UPDATE analyses SET status='failed', error_message='Interrupted by a server restart', completed_at=? WHERE status IN ('queued','running')", (now(),))

    def start(self, pid: str, provider: str = "auto") -> dict:
        if self.db.one("SELECT id FROM analyses WHERE project_id=? AND status IN ('queued','running')", (pid,)):
            raise ApiError(409, "analysis_running", "An analysis is already running for this project")
        try:
            prov = ai.pick(self.cfg, provider)
        except ai.AIError as e:
            raise ApiError(422, "provider_not_configured", e.message)
        aid = uid()
        stages = [{"key": k, "label": l, "status": "pending", "detail": ""} for k, l in STAGES]
        self.db.run("INSERT INTO analyses(id,project_id,status,mode,model_used,stages_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (aid, pid, "queued", "demo" if prov.id == "local" else "live", f"{prov.id}:{prov.model()}", json.dumps(stages), now()))
        projects.refresh_status(self.db, pid)
        self.pool.submit(self.run_inline, aid, provider)
        return self.summary(aid)

    def cancel(self, pid: str) -> None:
        self.db.run("UPDATE analyses SET cancel_requested=1 WHERE project_id=? AND status IN ('queued','running')", (pid,))

    def summary(self, aid: str):
        a = self.db.one("SELECT * FROM analyses WHERE id=?", (aid,))
        return None if not a else {"id": a["id"], "project_id": a["project_id"], "status": a["status"], "mode": a["mode"], "model_used": a["model_used"],
                                   "stage": a["stage"], "progress": a["progress"], "stages": json.loads(a["stages_json"]),
                                   "error_message": a["error_message"], "confidence": a["confidence"], "created_at": a["created_at"], "completed_at": a["completed_at"]}

    def run_inline(self, aid: str, provider: str = "auto") -> None:
        a = self.db.one("SELECT * FROM analyses WHERE id=?", (aid,))
        try:
            self._run(a, provider)
        except Cancelled:
            self._finish(a, "cancelled", "Stopped by you. Your brief and earlier results are untouched.")
        except ai.AIError as e:
            self._finish(a, "failed", FRIENDLY.get(e.code, "The AI step failed.") + (" " + e.message if e.code == "upstream" else ""))
        except AnalysisError as e:
            self._finish(a, "failed", str(e))
        except Exception as e:
            log.error("analysis crashed: %s", scrub(repr(e)), exc_info=False)
            self._finish(a, "failed", "Something went wrong on our side. Your brief is safe; try again.")

    def _finish(self, a, status, message):
        stages = json.loads(self.db.one("SELECT stages_json FROM analyses WHERE id=?", (a["id"],))["stages_json"])
        for s in stages:
            if s["status"] == "running":
                s["status"] = "failed" if status == "failed" else "skipped"
        self.db.run("UPDATE analyses SET status=?, error_message=?, stages_json=?, completed_at=? WHERE id=?",
                    (status, message[:400], json.dumps(stages), now(), a["id"]))
        projects.refresh_status(self.db, a["project_id"])

    def _run(self, a, provider):
        db, aid, pid = self.db, a["id"], a["project_id"]
        stages = json.loads(a["stages_json"])

        def stage(key, status, detail=""):
            for s in stages:
                if s["key"] == key:
                    s["status"], s["detail"] = status, detail[:300]
            done = sum(s["status"] in ("done", "skipped", "failed") for s in stages)
            db.run("UPDATE analyses SET stage=?, progress=?, stages_json=?, status=CASE WHEN status='queued' THEN 'running' ELSE status END WHERE id=?",
                   (key, done * 100 // len(stages), json.dumps(stages), aid))

        def check():
            if db.one("SELECT cancel_requested c FROM analyses WHERE id=?", (aid,))["c"]:
                raise Cancelled()

        proj, brief = db.one("SELECT * FROM projects WHERE id=?", (pid,)), projects.latest_brief(db, pid)
        if not brief:
            raise AnalysisError("Add a brief first.")
        stage("read", "running")
        text, flagged = split_injection(brief["extracted_content"])
        if len(text.strip()) < 30:
            raise AnalysisError("The brief is too short to analyze. Add more detail.")
        stage("read", "done", f"{len(text):,} characters" + (f"; ignored {len(flagged)} instruction-like line(s)" if flagged else ""))
        check()
        prov = ai.pick(self.cfg, provider)
        stage("extract", "running", f"Asking {prov.label}")
        spec = ai.extract_spec(prov, text, proj["competition_url"] or "")
        mode = "demo" if prov.id == "local" else "live"
        db.run("DELETE FROM requirements WHERE project_id=?", (pid,))
        for i, r in enumerate(spec["requirements"]):
            db.run("INSERT INTO requirements(id,project_id,analysis_id,ref,category,title,priority) VALUES(?,?,?,?,?,?,?)",
                   (uid(), pid, aid, r["id"], ai.categorize(r["text"]), r["text"], "must" if i < 3 else "should"))
        db.run("UPDATE analyses SET mode=?, model_used=?, confidence=? WHERE id=?", (mode, f"{prov.id}:{prov.model()}", spec["confidence"], aid))
        stage("extract", "done", f"{len(spec['requirements'])} requirements, confidence {int(spec['confidence'] * 100)}%")
        check()
        stage("build", "running", "Writing the app" if mode == "live" else "Generating the demo workbench")
        html = ai.build_app(prov, spec, text)
        stage("build", "done", f"{len(html) / 1000:.1f} KB, one file" + (" (demo workbench, not a domain solution)" if mode == "demo" else ""))
        check()

        def verify(h):
            stage("guard", "running")
            found = guard.scan_html(h)
            stage("guard", "failed" if found else "done", "Blocked: " + "; ".join(found) if found else "No unsafe APIs, tags or external resources")
            if found:
                stage("syntax", "skipped", "Not run until Code Guard passes")
                return found, ("skipped", "")
            stage("syntax", "running")
            syn = guard.check_js_syntax(h)
            stage("syntax", {"ok": "done", "fail": "failed"}.get(syn[0], "skipped"), syn[1])
            return found + ([f"JavaScript syntax error: {syn[1]}"] if syn[0] == "fail" else []), syn

        issues, syn = verify(html)
        repaired = False
        if issues and mode == "live":
            check()
            stage("repair", "running", "Asking the model to fix: " + issues[0][:120])
            html, repaired = ai.repair_app(prov, html, issues), True
            issues, syn = verify(html)
            stage("repair", "failed" if issues else "done", "Still failing after one repair" if issues else "Fixed and re-checked")
        else:
            stage("repair", "skipped", "Not needed" if not issues else "Demo mode never rewrites code")
        check()
        stage("package", "running")
        spec["coverage"] = guard.coverage(html)
        spec["checks"] = {"guard": [i for i in issues if not i.startswith("JavaScript")], "syntax": {"status": syn[0], "message": syn[1]},
                          "ok": not issues, "repaired": repaired}
        db.run("INSERT INTO artifacts(id,analysis_id,project_id,kind,content,checks_json,created_at) VALUES(?,?,?,?,?,?,?)",
               (uid(), aid, pid, "html-app", html, json.dumps(spec["checks"]), now()))
        db.run("UPDATE analyses SET result_json=?, explanation=?, implementation_prompt=? WHERE id=?",
               (json.dumps(spec), spec["explanation"], ai.implementation_prompt(spec, text), aid))
        if not proj["deadline"]:
            d = ai.parse_date(spec["deadline"]) if spec["deadline"] else ""
            if d:
                db.run("UPDATE projects SET deadline=? WHERE id=?", (d, pid))
        projects.seed_checklist(db, pid, spec["deliverables"])
        stage("package", "done", "Saved")
        stages_now = json.loads(db.one("SELECT stages_json FROM analyses WHERE id=?", (aid,))["stages_json"])
        db.run("UPDATE analyses SET status='completed', progress=100, completed_at=?, stages_json=? WHERE id=?", (now(), json.dumps(stages_now), aid))
        projects.refresh_status(db, pid)
