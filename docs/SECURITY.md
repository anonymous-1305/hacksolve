# Security

## Controls

| Threat | Control | Where | Tested |
|---|---|---|---|
| Weak or stolen passwords | scrypt (N=16384, r=8, p=1), per-user salt, constant-time compare, policy (10+ chars, mixed, not common), dummy hash for unknown emails | `security.py`, `auth.py` | yes |
| Session theft | 256-bit random token, only its SHA-256 stored, HttpOnly + SameSite=Strict cookie (+ Secure in production), expiry, logout deletes it | `auth.py`, `server.py` | yes |
| CSRF | custom `X-Requested-With` header required on every state-changing request + Origin check + JSON content type | `server.py` | yes |
| Regex denial of service | the prompt-injection patterns use bounded repetition (`{0,200}`) so a 60 KB line of `send send send ...` cannot stall a request | `security.py` | yes |
| Brute force and abuse | in-memory sliding-window limits on auth, analysis, upload; `Retry-After`; events logged | `security.py` | yes |
| Broken access control | every query scoped to the owning user; other users get 404 | `projects.py` | yes (all routes) |
| SQL injection | parameterised queries only | everywhere | by construction |
| XSS | UI escapes all dynamic text; strict CSP (`script-src 'self'`, no inline scripts, no eval); no third-party hosts | `app.js`, `server.py` | yes (browser) |
| Malicious uploads | extension allowlist, magic-byte match, size cap, binary rejection, safe display name, storage path built from a random id (never the filename) | `parsers.py`, `server.py` | yes |
| Zip bombs / zip-slip / XXE / macros (DOCX) | entry count, inflated size, ratio limits, unsafe path rejection, DOCTYPE/ENTITY rejection, `vbaProject.bin` rejection | `parsers.py` | yes |
| PDF and DOCX resource exhaustion | object-count, inflate, text-size, operand-stack and table caps; iteration budgets for character maps and width tables; linear-time object scan and line joining; a 10 s deadline checked inside the tokenizer; DOCX body capped at 8 MB; nothing is executed | `parsers.py` | yes (`test_hostile.py`: each case runs in a subprocess with a hard timeout) |
| Prompt injection | uploaded/pasted text is data: invisible characters stripped, instruction-like lines removed before any model sees them and logged, random-delimiter fences, output normalised and validated | `security.py`, `ai.py` | yes |
| Hostile generated code | static Code Guard (eval, Function, fetch, XHR, WebSocket, cookies, frames, external resources, bidi characters), JS syntax check, one repair attempt, flagged apps are never run | `guard.py`, `analysis.py` | yes |
| Generated code at runtime | served as its own document with `CSP: sandbox allow-scripts; default-src 'none'; connect-src 'none'` inside `<iframe sandbox="allow-scripts">`: no network, no cookies, no parent access | `server.py`, `app.js` | yes (real browser) |
| Secret leakage | keys only from env, never sent to the browser, redacted from logs/errors/security events | `config.py`, `security.py` | yes |
| SSRF | the server fetches no user-supplied URL; provider hosts are fixed (or operator-set `OPENAI_BASE_URL`, https or loopback only) | `ai.py` | yes |
| Clickjacking, sniffing | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: no-referrer` | `server.py` | yes |
| Slowloris / huge bodies | 30 s socket timeout, body caps, no chunked bodies | `server.py` | partly |

Security events (`security_events` table): `login_failed`, `rate_limited`, `csrf_blocked`, `prompt_injection`, `upload_rejected`.
Request logs contain method, path, status, duration and a short user id: never bodies, cookies or keys.

## Known limits (be aware)

- No email verification, password reset, MFA or account deletion.
- Rate limiter, job queue and sessions' cleanup are per process. Run one instance; use a shared store before scaling out.
- Registration reveals whether an email exists (needed for a clear error).
- Code Guard is a static filter, not a proof. The sandbox CSP is the real boundary; still read generated code before trusting it.
- Parsing runs in the request thread: a hostile file can occupy one CPU core for up to about 10 seconds before it is rejected.
- PDF parsing is best-effort. Unusual font encodings can produce garbled text; the parser then reports "no readable text".
- `node --check` runs only if Node is installed; otherwise the syntax stage says "skipped".
- Data is not encrypted at rest. Protect the `DATA_DIR` volume.
- Not penetration-tested; no external audit.
