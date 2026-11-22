"""Demo data for local development: one demo user and two analysed sample projects (demo mode)."""
from __future__ import annotations

import secrets

from . import auth, projects as P
from .db import uid

SAMPLES = [
    ("Offline Clinic Triage Assistant", "Offline Maternal Clinic Triage Assistant\nBuild a local-first intake and triage helper for rural health centers with intermittent connectivity.\n- Must work completely offline on low-spec hardware.\n- Must flag high-risk symptoms with explainable escalation rules and clinician sign-off.\n- Must never transmit patient identifiers externally.\nDeliverables: working prototype, public repository, 3-minute demo video.\nJudging criteria: Impact 40%, Feasibility 30%, Innovation 20%, Presentation 10%.\nSubmissions close on 5 December 2026."),
    ("Transit Route Optimizer", "Civic transit schedule optimizer\nBuild a tool that ranks bus feeder routes by commuter wait time for city commuters.\nMust run offline and keep all processing local.\nSubmit a prototype and a demo video by 12 Dec 2026.\nJudging criteria: Impact 50%, Feasibility 50%."),
]


def seed(app) -> str:
    email = "demo@hacksolve.local"
    if app.db.one("SELECT id FROM users WHERE email=?", (email,)):
        return f"Demo user already exists ({email}). Nothing changed."
    pw = secrets.token_urlsafe(12) + "A1"
    user, _ = auth.register(app.db, app.cfg, "Demo Student", email, pw, "seed")
    for title, text in SAMPLES:
        p = P.create(app.db, user["id"], title, "", None, None)
        P.set_brief(app.db, p, text, "paste", None, (user["id"], "seed"))
        aid = uid()
        import json
        from .analysis import STAGES
        from .db import now
        app.db.run("INSERT INTO analyses(id,project_id,status,mode,model_used,stages_json,created_at) VALUES(?,?,?,?,?,?,?)",
                   (aid, p["id"], "queued", "demo", "local:local-rules-v1", json.dumps([{"key": k, "label": l, "status": "pending", "detail": ""} for k, l in STAGES]), now()))
        app.analysis.run_inline(aid, "local")
    return f"Seeded. Log in with {email} and password {pw}  (shown once)"
