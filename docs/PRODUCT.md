# Product notes

**Who:** hackathon participants facing long briefs and short deadlines.
**Promise:** understand the brief, get a plan and a working demo app, rehearse, and submit with confidence.

## Journey
1. Landing, sign up or log in. 2. Dashboard of projects (status, progress, tags, deadline). 3. New project.
4. Paste text, drop a PDF/DOCX/TXT/MD, optionally add a link and deadline. 5. Analyze and build: live pipeline with real
stage status, Stop button, errors that keep your work. 6. Review: **Solution** (live sandboxed app), **Brief** (facts from
your brief), **Plan** (concept, priorities, MVP, editable explanation, coding prompt, simulated judge feedback), **Pitch and
rehearsal**, **Code**, **Submit** (checklist, countdown, export, delete).

## Facts versus suggestions
Blue tag "From your brief" = extracted. Amber tag "AI draft" or "Rule-based draft" = generated. Green "Yours" = your input.
Judge feedback is always labelled simulated. Demo mode is labelled on every result and builds a requirements workbench, not
a domain solution.

## Data model (SQLite)
`users`, `sessions`, `projects`, `briefs`, `file_uploads`, `analyses` (status, stages, mode, model, confidence, result_json,
explanation + version, implementation prompt, error), `requirements`, `artifacts` (generated app + checks), `checklist_items`,
`security_events`. Project status values: draft, analyzing, plan_ready, needs_attention, ready_to_build.

## Out of scope on purpose
Submitting for the user, verifying official rules, fetching competition links, OCR, real-time collaboration, OAuth.
