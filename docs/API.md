# API

Base path `/api`. JSON in, JSON out. Success: `{"data": ...}`. Error: `{"error": {"code", "message", "details?"}}`.

**Every `POST`, `PATCH`, `DELETE` must send** `X-Requested-With: HackSolve` (anti-CSRF) and, for JSON bodies,
`Content-Type: application/json`. Authentication is the `hs_session` cookie (HttpOnly, SameSite=Strict) set by register/login.

| Status | Meaning |
|---|---|
| 400 / 411 | malformed JSON, bad Content-Length, chunked body |
| 401 | not logged in or session expired |
| 403 | CSRF header missing or cross-origin request |
| 404 | not found **or not yours** (no existence leak) |
| 409 | email taken, analysis already running, no brief yet |
| 413 / 415 | body or file too large / wrong media type or file type |
| 422 | validation errors (`details` lists every field) or unreadable file |
| 429 | rate limited (`Retry-After` header) |

## Auth
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/auth/register` | `name, email, password` | 201 + cookie. Password: 10-128 chars, mixed, not common |
| POST | `/auth/login` | `email, password` | 200 + cookie. Same error for wrong password and unknown email |
| POST | `/auth/logout` | | 204, clears cookie, deletes the session |
| GET | `/auth/me` | | current user |
| PATCH | `/auth/me` | `name?, preferences?` | preferences is a small object |

## Projects
| Method | Path | Notes |
|---|---|---|
| GET | `/projects` | cards: status, progress, tags, deadline, checklist counts, latest analysis |
| POST | `/projects` | `title, description?, deadline? (YYYY-MM-DD), competition_url? (http/https)` |
| GET | `/projects/:id` | project, brief, requirements, checklist, latest analysis, files |
| PATCH | `/projects/:id` | any of title, description, deadline, competition_url (null clears) |
| DELETE | `/projects/:id` | 204; removes brief, uploads on disk, results |
| POST | `/projects/:id/brief` | `{text}` 20-60000 chars. Flags instruction-like lines (logged) |
| POST | `/projects/:id/files` | raw file body, `Content-Type: application/octet-stream`, `X-Filename` (URL-encoded). `.txt .md .pdf .docx`, 5 MB default. 201 `{file: {id, filename, size, truncated}, brief}` (`truncated` is true when only the first 60,000 characters were kept) |
| DELETE | `/projects/:id/artifacts` | 204; deletes analyses, generated apps, requirements. Keeps brief and checklist |
| GET | `/projects/:id/export` | JSON download of everything for the project |

## Analysis
| Method | Path | Notes |
|---|---|---|
| POST | `/projects/:id/analyze` | `{provider?: auto\|local\|anthropic\|openai\|gemini}` -> 202 `{analysis}`. 422 `provider_not_configured` if the named provider has no key |
| GET | `/projects/:id/analysis/status` | `{analysis: {status: queued\|running\|completed\|failed\|cancelled, stage, progress, stages[], error_message, mode: demo\|live, model_used}}` |
| GET | `/projects/:id/analysis` | `{latest, completed: {result, explanation, explanation_version, implementation_prompt, artifact: {content, checks}}, requirements}` |
| POST | `/projects/:id/analysis/cancel` | stops after the current stage |
| PATCH | `/analysis/:id` | `{explanation?, requirements?: [{id, status: open\|done\|blocked}]}` (explanation version increments) |
| POST | `/analysis/:id/regenerate` | new run for the same project |
| GET | `/projects/:id/preview` | the generated app as HTML under `CSP: sandbox allow-scripts; connect-src 'none'`. `?probe=1` adds an error reporter. Apps that fail Code Guard are replaced by a notice |

## Checklist and meta
| Method | Path | Notes |
|---|---|---|
| GET | `/projects/:id/checklist` | |
| POST | `/projects/:id/checklist` | `{title}` |
| PATCH | `/checklist/:id` | `status: todo\|done, title?, due_date?` |
| GET | `/providers` | `{providers: [{id, label, configured, model}], default}`. Never returns keys |
| GET/HEAD | `/api/health` | `{status, version, time}` (no auth) |

Project status: `draft`, `analyzing`, `plan_ready`, `needs_attention` (failed run or failed checks), `ready_to_build`
(checks passed and the whole checklist done).
