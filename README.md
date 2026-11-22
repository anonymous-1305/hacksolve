# HackSolve

**Understand the brief. Plan the build. Win the demo.**

Paste or upload a hackathon problem statement (text, PDF, DOCX, TXT, MD). HackSolve reads the rules, builds a working
one-file app that solves it, scans the code, syntax-checks it, previews it in a locked-down sandbox, and gives you the
requirements, plan, pitch, judge Q&A, rehearsal timer and a submission checklist. Accounts and projects are stored in a
real database.

Python standard library only. **No `pip install`, no Node, no build step.** Python 3.10+ (tested on 3.12).

## Run it (3 steps)

```bash
python run.py            # Windows: double-click run.bat   macOS/Linux: ./run.sh
```
1. Your browser opens **http://127.0.0.1:8000** (use `127.0.0.1`, not `localhost`).
2. Click **Start free**, create an account, create a project.
3. Paste a brief or drop a PDF/DOCX, press **Analyze and build**.

## Make it really solve problems (turn on an AI provider)

With no key, the server runs in **demo mode**: deterministic requirement extraction plus a requirements *workbench*.
It says so on every result and never pretends to be an AI. To build the real solution, add **one** of these to `.env`
(copy `.env.example` to `.env`, then restart):

| Option | Cost | `.env` |
|---|---|---|
| Google Gemini | has offered a free tier via Google AI Studio (check current limits) | `GEMINI_API_KEY=...` |
| Ollama (runs on your laptop) | free | `OPENAI_BASE_URL=http://127.0.0.1:11434/v1` and `OPENAI_MODEL=llama3.1` |
| Anthropic Claude | paid | `ANTHROPIC_API_KEY=...` |
| OpenAI | paid | `OPENAI_API_KEY=...` |

Check it with `python run.py check`. Model names change; if a provider says "model not found", update `*_MODEL`.
A failing live provider is reported as a failure. It is **never** silently replaced by demo output.
Small local models may produce weaker apps; the pipeline checks and repairs once, then tells you what still fails.

## Commands

```bash
python run.py              # serve
python run.py migrate      # create/upgrade the database schema
python run.py seed         # demo user + 2 analysed sample projects (refused when APP_ENV=production)
python run.py check        # show which AI providers are configured
python -m unittest discover -s tests -v        # 103 unit + API tests, standard library only
python tests/e2e_browser.py                    # browser end-to-end (pip install playwright && playwright install chromium)
```

## What is in the box

```
run.py                 launcher + CLI          web/            the UI (index.html, app.css, app.js)
hacksolve/config.py    env-based settings      hacksolve/db.py       SQLite, WAL, migrations
hacksolve/security.py  hashing, limits, cleaning  hacksolve/auth.py  accounts + sessions
hacksolve/parsers.py   TXT/MD/DOCX/PDF         hacksolve/guard.py    Code Guard + syntax check
hacksolve/ai.py        providers + demo engine hacksolve/analysis.py background pipeline
hacksolve/projects.py  projects, checklist     hacksolve/server.py   HTTP API + static files
tests/                 unit, API, hostile-input, browser tests   docs/              API, security, deployment, test results
```

Pipeline stages (all visible live in the UI): read brief, extract requirements and plan, build app, Code Guard scan,
syntax check, repair once if needed, package and save.

## Troubleshooting

- **Open `http://127.0.0.1:8000`** (not `localhost`; some systems try IPv6 first). If port 8000 is taken, HackSolve uses the next free port and prints it.
- **"No readable text found" on a PDF:** it is scanned or uses a font without a Unicode map. Paste the text, or re-export the document to PDF.
- **"AI provider isn't configured":** the server has no key for that provider. Pick Demo mode, or add a key to `.env` and restart (`python run.py check` shows what is configured).
- **Python not found on Windows:** install from python.org and tick "Add Python to PATH". `run.bat` tries `py -3` then `python`.
- **Nothing is slow on first load:** the page makes no third-party requests (no web fonts), by design.

## Settings

All in `.env.example`: server (`HOST`, `PORT`, `DATA_DIR`, `MAX_UPLOAD_MB`, `SESSION_DAYS`, `COOKIE_SECURE`,
`TRUST_PROXY`, `ALLOWED_ORIGINS`), rate limits, `WORKERS`, and the provider keys/models above.

## Honest scope: how this differs from the original brief

- **Stack:** Python stdlib + SQLite + vanilla JS, not React/TypeScript/Tailwind/Express/PostgreSQL/Prisma. Reason: it runs
  with zero installs and every test could actually be run. A Postgres port means replacing `hacksolve/db.py`.
- **Sign-in:** email and password only. Google/GitHub buttons are shown as unavailable. No password reset, email
  verification or account deletion yet (they need an email service).
- **Jobs:** an in-process thread pool, not Redis/Celery. Progress is polled, not pushed.
- **Data model:** the plan, explanation, prompt and judge feedback live in the analysis record (`result_json`, with a
  versioned editable explanation) rather than four separate tables.
- **PDF:** reads text-based PDFs from Word, Google Docs/Chrome, LibreOffice, wkhtmltopdf, LaTeX and reportlab (tested on real files from each, including Hindi text from Chrome). No OCR: scanned, encrypted, or unusual-font PDFs get a clear message asking you to paste the text. Long files use the first 60,000 characters (the UI tells you). Reading is size- and time-limited.
- **Links:** competition URLs are stored, never fetched.
- **Single instance:** rate limits and jobs are in memory, so run one instance (see `docs/DEPLOYMENT.md`).

See `docs/SECURITY.md` for controls and limits, `docs/API.md` for the API, `docs/TEST_RESULTS.md` for what was run.
