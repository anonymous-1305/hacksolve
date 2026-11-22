# Changelog

## 1.0.1: debugging pass
Fixed (details and evidence in `docs/TEST_RESULTS.md` section 4):
- **PDF reading rewritten** for real-world files (Chrome/Google Docs, LibreOffice, wkhtmltopdf, LaTeX, reportlab): from 91-95% accuracy, empty or garbled output, to 100% on the test files.
- **Phone layout**: content no longer touches the screen edges; header buttons no longer squashed.
- **Server hangs**: five crafted inputs could stall the server (one for over 5 minutes); all are bounded and tested.
- **Windows/macOS**: binary-safe uploads, PBKDF2 fallback when Python lacks scrypt, busy-port fallback, `run.bat` improvements, UTF-16 text files.
- **Hindi/Indic PDFs**: read in the correct order (honours `/ActualText`), one line per sentence, plus a reading-order notice.
- Smaller: HEAD requests, keep-alive after DELETE, nested-JSON 400, `/api/providers` crash with a key-less provider, long-file notice, light-mode contrast.

## 1.0.0
First release: accounts, SQLite database, PDF/DOCX/TXT/MD upload, background build pipeline with Code Guard and sandboxed preview, demo mode and live AI providers, web UI, tests.
