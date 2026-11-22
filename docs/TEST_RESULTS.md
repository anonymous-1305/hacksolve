# Test results

Run on 2026-11-21 in the build sandbox: Python 3.12.3, Node v22.23.4 (used only by the syntax-check stage), headless Chromium via Playwright. These are the real outputs of the commands below. Version 1.0.1 (debugging pass).

## 1. Unit and API tests (standard library only)

`python -m unittest discover -s tests -v`  ->  **Ran 103 tests in 26.566s, OK**

| File | Tests | Covers |
|---|---|---|
| test_security.py | 8 | hashing (scrypt and PBKDF2 fallback), password policy, rate limiter, text cleaning, injection removal, filenames, secret redaction |
| test_parsers.py | 30 | TXT/MD/UTF-16/DOCX/PDF extraction, **real files from ten generators (including Hindi text)**, text-position and ActualText mechanics, magic bytes, zip bomb, zip-slip, macros, XXE, encrypted/scanned PDFs, page order, truncation flag, line re-joining |
| test_hostile.py | 4 | pathological input must finish fast: regex DoS, PDFs with no `endobj`, endless character maps, huge width tables, operand floods (each in a subprocess with a hard timeout) |
| test_guard.py | 10 | Code Guard rules, false-positive check, JS syntax check, local engine, output normalising |
| test_providers.py | 9 | Anthropic/OpenAI/Gemini request and response shapes (faked transport), status/timeout mapping on a real socket, key never in URL or errors |
| test_api.py | 42 | accounts, sessions, CSRF, validation, every protected route, cross-user isolation, uploads (byte-for-byte storage), full analysis pipeline, sandbox headers, checklist, export/delete, live-provider path against a fake server, misconfigured provider, migrations/seed/CLI, static assets, keep-alive/HEAD/busy-port, nested-JSON bomb, rate limits |

## 2. Browser end-to-end (real Chromium)

`python tests/e2e_browser.py`  ->  **19 steps passed; no console errors, no CSP violations, no page errors**

1. first paint in 0.29s with zero third-party requests
2. landing renders, hero 3D scene present, a11y basics, no inline scripts
3. keyboard focus ring is visible
4. theme (system/light/dark) and motion toggles work
5. signup validates, shows password strength, lands on empty dashboard
6. analysis runs, pipeline shows stages, solution renders in sandboxed frame, browser sandbox check passes
7. generated app cannot use the network, read cookies, or reach the parent page
8. brief/plan/pitch/code/submit tabs: edit+save explanation, copy, timer, download, checklist, countdown
9. light and dark themes resolve to the right colours; text/tag/button contrast >= 4.5:1 (WCAG AA)
10. PDF and DOCX upload fill the brief; wrong types and fake PDFs are rejected with clear messages
11. prompt-injection line is flagged in the UI and ignored by the pipeline
12. 375px phone layout: no overflow, content clear of screen edges, header buttons not squashed (7 screens, logged in and out)
13. delete project, settings, logout, and protected routes redirect to login
14. login: wrong password gives one clear error, the right one reaches the dashboard
15. a real LibreOffice PDF uploads, is read correctly, and its deadline and judging criteria are extracted
16. live mode: provider picker, Live AI label, Stop cancels and keeps the brief
17. live mode: model-built app renders, brief tab shows AI extraction and where the app covers each requirement
18. live failure is shown plainly, never replaced by demo output, and the earlier result is preserved
19. recovery after failure, JSON export download, and deleting generated results

Measured WCAG contrast ratios (AA needs 4.5 for small text): light/warning banner=6.2, light/success banner=6.1, light/heading=17.9, light/muted text=5.7, light/blue tag=4.6, light/amber tag=6.2, light/primary button=5.4, light/status badge=5.4, dark/warning banner=9.0, dark/success banner=8.7, dark/heading=15.2, dark/muted text=6.7, dark/blue tag=5.3, dark/amber tag=9.0, dark/primary button=6.6, dark/status badge=9.6

Accessibility here means automated basics (labels, names, lang, title, focus ring, no inline scripts, contrast), **not** a full audit such as axe or a screen-reader pass.

## 3. Real-world file accuracy (word recall / precision against the known text)

Files made by real tools (kept in `tests/fixtures`); the test fails below 97% on either number.

| File | Made by | Before the rewrite | Now |
|---|---|---|---|
| chromium.pdf | Chrome / Google Docs (Skia) | 95% / 91% | **100% / 100%** |
| chromium_objstm.pdf | same, PDF 1.5 object streams | 95% / 91% | **100% / 100%** |
| libreoffice.pdf | LibreOffice | 91% / 88% | **100% / 100%** |
| wkhtml.pdf | wkhtmltopdf (Qt) | unreadable | **100% / 100%** |
| pdftex.pdf | pdfTeX (LaTeX) | 91% / 85% | **100% / 99%** |
| reportlab.pdf | reportlab (ASCII85+Flate) | nothing read | **100% / 100%** |
| word_like.docx | python-docx (Word-style XML) | n/a (already exact) | **100% / 100%** |
| libreoffice.docx | LibreOffice | n/a (already exact) | **100% / 100%** |
| pandoc.docx | pandoc | n/a (already exact) | **100% / 100%** |

PDFs were also checked against `pdftotext` (poppler): identical output for Hindi from Chrome (vowel signs in logical order, via `/ActualText`), and 100% on Chrome, LibreOffice, wkhtmltopdf, reportlab, pdfTeX and a 399-section, 259 KB document (parsed in about 0.5 s). Multi-page order is preserved. Image-only (scanned) PDFs give a clear "paste the text" message.

## 4. Bugs found and fixed in the debugging pass (all now have regression tests)

**Would have hit a student first**
- PDF reading was only tested on hand-built PDFs. Real files lost up to 9% of words (Chrome, LibreOffice, LaTeX), returned nothing (reportlab) or garbage (wkhtmltopdf). Rewritten: page tree, per-font character maps, object streams, ASCII85/hex/predictor filters, glyph widths for word spacing, TeX ligatures and hyphenation, line re-joining.
- Phones and tablets: page content touched the screen edges (a padding rule reset left/right padding), header buttons were squashed, one hero card was clipped, project cards got double padding.
- Windows/macOS: uploads could be corrupted on Windows (no binary flag), Python builds without `scrypt` could not start (PBKDF2 fallback added), a busy port crashed on start (now uses the next free one), `run.bat` now finds `py` or `python`, UTF-16 text files are readable, console encoding forced to UTF-8.
- Long PDFs were silently cut at 60,000 characters; the UI now says so.
- **Hindi PDFs** (found by a last-minute check) came out one word per paragraph with vowel signs misplaced. Now identical to `pdftotext`: the reader honours `/ActualText` and compares real page positions across text blocks (tracking transform matrices). PDFs with Indic, Arabic or Hebrew text also show a "check the reading order" notice.

**Security and robustness (found by attacking my own code)**
- A crafted 69 KB PDF hung the server for over 5 minutes (quadratic line joining). Three more hang conditions: the prompt-injection regex (`send send send ...`), PDFs with many `obj` and no `endobj`, endless ToUnicode maps. Also memory floods (operand stack) and width-table loops. All bounded now, including a deadline inside the tokenizer.
- Keep-alive connections desynchronised after a DELETE with a body; HEAD returned 501; deeply nested JSON gave a 500 instead of 400; `/api/providers` crashed when `AI_PROVIDER` named a provider with no key.

**Mistakes I introduced and then caught**
- My speed fix briefly corrupted the dark/light theme CSS (found by looking at a screenshot); the contrast test then found light-mode amber and mint below WCAG AA. Both are fixed and guarded by tests.

## 5. Performance (the "10 second" load)

Cause: a render-blocking Google Fonts `<link>`. With that host made to hang for 10 s, first contentful paint was 10.4 s (app) and 10.1 s (published page). After removing every third-party request: 0.2 s for both. Guarded by a browser test (under 2 s with hanging hosts, no request leaves 127.0.0.1) and a unit test (no third-party URLs in HTML, CSS, JS or CSP).

## 6. Do the tests catch regressions? (mutation check)

Each control below was deliberately removed in a throwaway copy; the suite failed every time: CSRF check (1 failure), per-user scoping (18 failures + 2 errors), prompt-injection removal (2), password verification (1), Code Guard `fetch` rule (2), DOCX macro rejection (1).

## 7. Smoke test of the documented flow

From a clean copy: `python run.py migrate`, `seed`, `check`, `python run.py --no-browser`, login as the seeded user with curl, list projects (2 x "plan_ready"), load `/` (200, under 1 ms). The server log contained no passwords or session tokens.

## 8. Not tested (be honest with yourself before relying on it)

- Anthropic and Gemini were never called for real (no network or keys here). Their request/response shapes follow the providers' documented formats and are unit-tested with fakes only. The OpenAI-compatible path was tested end to end against a local fake server. Model names in `.env.example` may need updating.
- PDFs from Microsoft Word, macOS Preview/Quartz or Adobe tools. Non-Latin scripts other than Hindi produced by Chrome (other generators may order complex scripts differently). Word-style DOCX was tested via python-docx and LibreOffice, not real Word.
- Dockerfile, `render.yaml` and the Railway/Fly steps were not executed.
- Python versions other than 3.12; Windows and macOS (the Windows fixes are by construction, not run). Browsers other than Chromium.
- Load, concurrency beyond a few parallel requests, long-running soak behaviour.
- Penetration testing or any external security review.
