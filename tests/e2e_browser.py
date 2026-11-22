"""Browser end-to-end test (needs: pip install playwright && playwright install chromium).
Run from the project root:  python tests/e2e_browser.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from helpers import BRIEF, INJECTED, Env, FakeProvider, make_docx, make_pdf  # noqa: E402

SHOTS = os.environ.get("SHOTS", os.path.join(__import__("tempfile").gettempdir(), "hacksolve-shots"))
os.makedirs(SHOTS, exist_ok=True)
env = Env()
base = f"http://127.0.0.1:{env.port}"
problems, steps = [], []


def step(name):
    steps.append(name)
    print(f"  ok  {name}")


def rgb(c):
    return [int(float(x)) for x in re.findall(r"[\d.]+", c)[:3]]


def lum(c):
    ch = [v / 255 for v in rgb(c)]
    ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(fg, bg):
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


COLORS_JS = """(sel) => { const el = document.querySelector(sel); if (!el) return null;
  let n = el, bg = null; while (n) { const b = getComputedStyle(n).backgroundColor; if (b !== 'rgba(0, 0, 0, 0)') { bg = b; break; } n = n.parentElement; }
  return [getComputedStyle(el).color, bg || getComputedStyle(document.body).backgroundColor]; }"""


def a11y(page):
    return page.evaluate("""() => { const bad=[];
      document.querySelectorAll('input,textarea,select').forEach(el=>{ if(el.type==='hidden') return;
        const named=el.getAttribute('aria-label')||(el.id&&document.querySelector('label[for="'+el.id+'"]'))||el.closest('label');
        if(!named) bad.push('unlabelled '+el.tagName+'#'+el.id); });
      document.querySelectorAll('button,a').forEach(el=>{ if(!(el.textContent.trim()||el.getAttribute('aria-label')||el.title)) bad.push('unnamed '+el.tagName); });
      if(!document.documentElement.lang) bad.push('no lang'); if(!document.title) bad.push('no title');
      if(document.querySelectorAll('script:not([src])').length) bad.push('inline script (CSP)');
      return bad; }""")


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        def on_console(m):
            t = m.text
            expected_probe = "/api/health" in t and "Content Security Policy" in t  # our deliberate sandbox probe
            if m.type == "error" and not expected_probe and not ("Failed to load resource" in t and "status of 5" not in t):
                problems.append("console: " + t)

        page.on("console", on_console)
        page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))

        # ---- first paint must not depend on any other website (a slow font host once cost 10 seconds)
        import time
        perf = browser.new_context(bypass_csp=True).new_page()
        hosts = set()
        perf.on("request", lambda r: hosts.add(r.url.split("/")[2]))
        t0 = time.time()
        perf.goto(base, wait_until="commit")
        perf.wait_for_function("performance.getEntriesByName('first-contentful-paint').length>0", timeout=15000)
        fcp = time.time() - t0
        assert fcp < 2.0, f"first contentful paint took {fcp:.1f}s"
        assert hosts == {f"127.0.0.1:{env.port}"}, f"third-party requests on first load: {hosts}"
        perf.close()
        step(f"first paint in {fcp:.2f}s with zero third-party requests")

        # ---- landing
        page.goto(base)
        expect(page.get_by_role("heading", level=1)).to_contain_text("Understand the brief. Plan the build. Win the demo.")
        page.wait_for_timeout(1900)
        page.screenshot(path=f"{SHOTS}/01-landing-light.png")
        assert not a11y(page), a11y(page)
        step("landing renders, hero 3D scene present, a11y basics, no inline scripts")
        page.keyboard.press("Tab")
        assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
        step("keyboard focus ring is visible")
        page.click("#mo")
        assert page.evaluate("document.documentElement.dataset.motion") == "reduced"
        assert page.evaluate("getComputedStyle(document.querySelector('.rv')).opacity") == "1"
        page.click("#mo")
        page.click("#th")
        page.click("#th")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.screenshot(path=f"{SHOTS}/02-landing-dark.png")
        page.click("#th")
        step("theme (system/light/dark) and motion toggles work")

        # ---- signup
        page.get_by_role("link", name="Start free").first.click()
        expect(page.get_by_role("heading", name="Create your account")).to_be_visible()
        page.fill("#name", "Test Student")
        page.fill("#email", "student@example.com")
        page.fill("#password", "abc")
        expect(page.locator("#mtxt")).to_contain_text("Weak")
        page.click("#asub")
        expect(page.locator("#e-password")).to_contain_text("at least 10")
        page.fill("#password", "CorrectHorse#2026")
        expect(page.locator("#mtxt")).to_contain_text("Strong")
        page.screenshot(path=f"{SHOTS}/03-signup.png")
        assert not a11y(page), a11y(page)
        page.click("#asub")
        expect(page.get_by_role("heading", name="Welcome back, Test")).to_be_visible()
        expect(page.get_by_text("No projects yet")).to_be_visible()
        page.screenshot(path=f"{SHOTS}/04-dashboard-empty.png")
        step("signup validates, shows password strength, lands on empty dashboard")

        # ---- new project, paste brief, analyze
        page.fill("#ntitle", "Clinic Triage 2026")
        page.click("#np button")
        expect(page.locator("#ptitle")).to_have_value("Clinic Triage 2026")
        page.fill("#brief", BRIEF)
        assert not a11y(page), a11y(page)
        page.click("#goBtn")
        expect(page.get_by_role("tab", name="Solution")).to_be_visible(timeout=20000)
        expect(page.locator("#pipe")).to_contain_text("Code Guard scan")
        expect(page.locator("#pipe")).to_contain_text("Demo mode, no AI model")
        frame = page.frame_locator("iframe.pv")
        expect(frame.get_by_text("Demo mode.")).to_be_visible(timeout=10000)
        expect(frame.get_by_text("Must work completely offline").first).to_be_visible()
        expect(page.locator("#runcheck")).to_have_text("Ran in your browser without errors.", timeout=10000)
        page.screenshot(path=f"{SHOTS}/05-results-solution.png", full_page=True)
        step("analysis runs, pipeline shows stages, solution renders in sandboxed frame, browser sandbox check passes")

        pf = next(f for f in page.frames if f != page.main_frame and "preview" in f.url)
        assert pf.evaluate("(async()=>{try{await fetch('/api/health');return 'allowed'}catch(e){return 'blocked'}})()") == "blocked"
        assert pf.evaluate("(()=>{try{document.cookie;return 'readable'}catch(e){return 'blocked'}})()") == "blocked"
        assert pf.evaluate("(()=>{try{parent.document.title;return 'readable'}catch(e){return 'blocked'}})()") == "blocked"
        step("generated app cannot use the network, read cookies, or reach the parent page")

        # ---- tabs
        page.get_by_role("tab", name="Brief").click()
        expect(page.get_by_text("From your brief").first).to_be_visible()
        expect(page.locator("#tabbody")).to_contain_text("2026-12-05")
        page.get_by_role("tab", name="Plan").click()
        page.fill("#expl", "I am planning to build my own words version.")
        page.click("#tabbody h4 >> text=Solution concept")
        expect(page.locator("#toast")).to_contain_text("Explanation saved")
        expect(page.get_by_text("Simulated", exact=True)).to_be_visible()
        page.get_by_role("button", name="Copy prompt").click()
        expect(page.locator("#toast")).to_contain_text("Prompt copied")
        page.screenshot(path=f"{SHOTS}/06-plan.png", full_page=True)
        page.get_by_role("tab", name="Pitch and rehearsal").click()
        page.click("#tstart")
        page.wait_for_timeout(1300)
        assert page.inner_text("#tm") in ("2:59", "2:58")
        page.click("#tstart")
        page.fill("#notes", "Open with the clinic story.")
        page.get_by_role("tab", name="Code").click()
        expect(page.locator("pre")).to_contain_text("<!doctype html")
        with page.expect_download() as dl:
            page.get_by_role("button", name="Download .html").click()
        assert dl.value.suggested_filename == "clinic-triage-2026.html"
        page.get_by_role("tab", name="Submit").click()
        expect(page.locator("#cd")).to_contain_text("d ")
        first = page.locator("[data-act=chk]").first
        first.check()
        expect(page.locator("#pbadge")).to_contain_text("Plan ready")
        page.fill("#ctitle", "Book the demo table")
        page.click("#addc button")
        expect(page.get_by_text("Book the demo table")).to_be_visible()
        page.screenshot(path=f"{SHOTS}/07-submit.png", full_page=True)
        step("brief/plan/pitch/code/submit tabs: edit+save explanation, copy, timer, download, checklist, countdown")

        # ---- both themes: right colours (guards a once-corrupted stylesheet) and WCAG AA contrast
        expect_bg = {"light": ("rgb(246, 241, 230)", "rgb(255, 255, 255)"), "dark": ("rgb(13, 16, 22)", "rgb(23, 27, 35)")}
        report = []
        for theme in ("light", "dark"):
            while page.evaluate("document.documentElement.dataset.theme") != theme:
                page.click("#th")
            for tab, items in [("Solution", [("warning banner", "#tabbody .ban.bad"), ("success banner", "#tabbody .ban:not(.bad)")]),
                               ("Brief", [("heading", ".card h2"), ("muted text", "#res .mut"), ("blue tag", "#tabbody .tag:not(.s):not(.y)"),
                                          ("amber tag", "#pipe .tag.s"), ("primary button", ".btn.p"), ("status badge", ".badge.plan_ready")])]:
                page.get_by_role("tab", name=tab).click()
                body_bg, card_bg = page.evaluate("[getComputedStyle(document.body).backgroundColor, getComputedStyle(document.querySelector('.card')).backgroundColor]")
                assert (body_bg, card_bg) == expect_bg[theme], f"{theme}: page {body_bg}, card {card_bg}"
                for label, sel in items:
                    pair = page.evaluate(COLORS_JS, sel)
                    assert pair, f"{theme}: no element for {label} ({sel}) on the {tab} tab"
                    ratio = contrast(*pair)
                    report.append(f"{theme}/{label}={ratio:.1f}")
                    assert ratio >= 4.5, f"{theme}: {label} contrast {ratio:.2f} (< 4.5)"
        while page.evaluate("document.documentElement.dataset.theme") != "light":
            page.click("#th")
        print("      contrast ratios: " + ", ".join(report))
        step("light and dark themes resolve to the right colours; text/tag/button contrast >= 4.5:1 (WCAG AA)")

        # ---- uploads
        lines = ["Rural Triage", "Build an offline triage helper for health workers.", "Must never transmit patient data."]
        page.set_input_files("#file", files=[{"name": "brief.pdf", "mimeType": "application/pdf", "buffer": make_pdf(lines, compress=True)}])
        expect(page.locator("#upmsg")).to_contain_text("Read")
        expect(page.locator("#brief")).to_have_value(re.compile("offline triage helper"))
        dlines = ["Word Brief", "Build a Word-uploaded feature for the demo.", "Must support DOCX parsing."]
        page.set_input_files("#file", files=[{"name": "brief.docx", "mimeType": "x", "buffer": make_docx(dlines)}])
        expect(page.locator("#brief")).to_have_value(re.compile("Word-uploaded feature"))
        page.set_input_files("#file", files=[{"name": "evil.exe", "mimeType": "x", "buffer": b"MZ..."}])
        expect(page.locator("#toast")).to_contain_text("Use a PDF, DOCX, TXT or MD file")
        page.set_input_files("#file", files=[{"name": "fake.pdf", "mimeType": "application/pdf", "buffer": b"this is not a pdf at all"}])
        expect(page.locator("#toast")).to_contain_text("doesn't match")
        step("PDF and DOCX upload fill the brief; wrong types and fake PDFs are rejected with clear messages")

        # ---- prompt injection is surfaced
        page.fill("#brief", INJECTED)
        page.click("#goBtn")
        expect(page.locator("#flags")).to_contain_text("1 instruction-like line found", timeout=10000)
        expect(page.locator("#pipe")).to_contain_text("ignored 1 instruction-like", timeout=15000)
        step("prompt-injection line is flagged in the UI and ignored by the pipeline")

        # ---- mobile layout, dark results
        proj_hash = "#" + page.url.split("#", 1)[1]
        mctx = browser.new_context(viewport={"width": 375, "height": 812}, storage_state=ctx.storage_state(), device_scale_factor=2)
        octx = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2)
        m = mctx.new_page()
        LAYOUT_JS = """() => { const w = innerWidth, bad = [];
          document.querySelectorAll('#view .card, #view h1, #view h2, #view .ph, #view form.row, #view .empty').forEach(el => { const r = el.getBoundingClientRect();
            if (r.width > 0 && (r.left < 12 || r.right > w - 12)) bad.push(el.tagName + '.' + el.className + ' [' + Math.round(r.left) + ',' + Math.round(r.right) + ']'); });
          document.querySelectorAll('header .btn').forEach(el => { const r = el.getBoundingClientRect(); if (r.width && (r.width < 36 || r.height < 36)) bad.push('squashed header button ' + (el.id || el.textContent.trim()) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height)); });
          return {over: document.documentElement.scrollWidth - document.documentElement.clientWidth, bad}; }"""
        for hash_, name, ctx_ in [("#/", "landing", mctx), ("#/app", "dashboard", mctx), (proj_hash, "project", mctx), ("#/settings", "settings", mctx),
                                  ("#/", "landing-out", octx), ("#/signup", "signup-out", octx), ("#/login", "login-out", octx)]:
            m = ctx_.new_page()
            m.goto(base + "/" + hash_)
            m.wait_for_timeout(1700 if name.startswith("landing") else 800)
            res = m.evaluate(LAYOUT_JS)
            assert res["over"] <= 1, f"horizontal overflow on {name}: {res['over']}px"
            assert not res["bad"], f"layout problems on {name} at 375px: {res['bad']}"
            m.screenshot(path=f"{SHOTS}/m-{name}.png")
            m.close()
        step("375px phone layout: no overflow, content clear of screen edges, header buttons not squashed (7 screens, logged in and out)")
        page.goto(base + "/#/app")
        expect(page.get_by_text("Clinic Triage 2026")).to_be_visible()
        page.click("#th"); page.click("#th")
        page.screenshot(path=f"{SHOTS}/08-dashboard-dark.png")
        page.click("#th")

        # ---- delete project (two-step), logout, protected routes
        page.get_by_role("button", name="Delete").click()
        page.get_by_role("button", name="Confirm delete").click()
        expect(page.get_by_text("No projects yet")).to_be_visible()
        page.goto(base + "/#/settings")
        expect(page.get_by_role("heading", name="Settings")).to_be_visible()
        expect(page.get_by_text("Configured").first).to_be_visible()
        page.get_by_role("button", name="Log out").click()
        expect(page.get_by_role("heading", level=1)).to_contain_text("Understand the brief")
        page.goto(base + "/#/app")
        expect(page.get_by_role("heading", name="Welcome back")).to_be_visible()
        step("delete project, settings, logout, and protected routes redirect to login")

        # ---- login through the UI
        page.fill("#email", "student@example.com")
        page.fill("#password", "WrongPassword#1")
        page.click("#asub")
        expect(page.locator("#aerr")).to_contain_text("incorrect")
        page.fill("#password", "CorrectHorse#2026")
        page.click("#asub")
        expect(page.get_by_role("heading", name="Welcome back, Test")).to_be_visible()
        step("login: wrong password gives one clear error, the right one reaches the dashboard")

        # ---- a real LibreOffice-made PDF, uploaded in the browser, analysed end to end
        fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        page.fill("#ntitle", "Real PDF brief")
        page.click("#np button")
        expect(page.locator("#ptitle")).to_have_value("Real PDF brief")
        page.set_input_files("#file", files=[{"name": "libreoffice.pdf", "mimeType": "application/pdf", "buffer": open(os.path.join(fixtures, "libreoffice.pdf"), "rb").read()}])
        expect(page.locator("#brief")).to_have_value(re.compile("Offline Clinic Triage Assistant"))
        expect(page.locator("#brief")).to_have_value(re.compile("5 December 2026"))
        page.click("#goBtn")
        expect(page.get_by_role("tab", name="Brief")).to_be_visible(timeout=20000)
        page.get_by_role("tab", name="Brief").click()
        expect(page.locator("#tabbody")).to_contain_text("2026-12-05")
        expect(page.locator("#tabbody")).to_contain_text("Impact 40%")
        step("a real LibreOffice PDF uploads, is read correctly, and its deadline and judging criteria are extracted")

        # ---- live-AI mode in the UI (fake OpenAI-compatible server): Stop, Try again, failure keeps earlier results, Regenerate, export, delete
        fake = FakeProvider("ok", delay=1.5)
        keys = {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "test-key-1234567890", "GEMINI_API_KEY": ""}
        env2 = Env(ai_provider="openai", openai_base_url=fake.url, keys=keys, models={"anthropic": "x", "openai": "fake-model", "gemini": "x"})
        try:
            base2 = f"http://127.0.0.1:{env2.port}"
            c2 = env2.client()
            c2.signup("Live Tester")
            pid2 = c2.project("Live project")
            ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
            ctx2.add_cookies([{"name": "hs_session", "value": c2.cookie.split("=", 1)[1], "url": base2}])
            p2 = ctx2.new_page()
            p2.on("pageerror", lambda e: problems.append("pageerror(live): " + str(e)))
            p2.goto(f"{base2}/#/p/{pid2}")
            expect(p2.locator("#prov option")).to_have_count(3)
            p2.select_option("#prov", "openai")
            p2.click("#goBtn")
            expect(p2.locator("#pipe")).to_contain_text("Live AI: openai:fake-model")
            expect(p2.locator("#stopBtn")).to_be_visible()
            p2.click("#stopBtn")
            expect(p2.locator("#pipe .ban")).to_contain_text("Stopped by you", timeout=15000)
            expect(p2.locator("#brief")).not_to_have_value("")
            step("live mode: provider picker, Live AI label, Stop cancels and keeps the brief")
            p2.get_by_role("button", name="Try again").click()
            expect(p2.get_by_role("tab", name="Solution")).to_be_visible(timeout=20000)
            expect(p2.frame_locator("iframe.pv").get_by_text("LIVE-FAKE-APP triage")).to_be_visible()
            expect(p2.locator("#tabbody")).not_to_contain_text("Demo mode built")
            p2.get_by_role("tab", name="Brief").click()
            expect(p2.locator("#tabbody")).to_contain_text("AI extraction")
            expect(p2.locator("#tabbody")).to_contain_text("works offline, no network calls")
            step("live mode: model-built app renders, brief tab shows AI extraction and where the app covers each requirement")
            fake.mode = "fail"
            p2.get_by_role("tab", name="Code").click()
            p2.get_by_role("button", name="Regenerate").click()
            expect(p2.locator("#pipe .ban")).to_contain_text("provider", timeout=20000)
            expect(p2.locator("#pbadge")).to_contain_text("Needs attention")
            expect(p2.get_by_role("tab", name="Solution")).to_be_visible()
            expect(p2.get_by_role("button", name="Try again")).to_be_visible()
            p2.get_by_role("tab", name="Code").click()
            expect(p2.locator("pre")).to_contain_text("LIVE-FAKE-APP")
            step("live failure is shown plainly, never replaced by demo output, and the earlier result is preserved")
            fake.mode = "ok"
            p2.get_by_role("button", name="Try again").click()
            expect(p2.locator("#pbadge")).to_contain_text("Plan ready", timeout=25000)
            p2.get_by_role("tab", name="Submit").click()
            with p2.expect_download() as dl2:
                p2.get_by_role("link", name="Export project JSON").click()
            assert dl2.value.suggested_filename == "hacksolve-live-project.json", dl2.value.suggested_filename
            import json as _json
            exported = _json.load(open(dl2.value.path()))["data"]
            assert exported["project"]["title"] == "Live project" and "LIVE-FAKE-APP" in exported["artifacts"][0]["content"]
            p2.get_by_role("button", name="Delete generated results").click()
            p2.get_by_role("button", name="Tap again to delete results").click()
            expect(p2.locator("#res")).to_be_hidden()
            expect(p2.locator("#pbadge")).to_contain_text("Draft")
            step("recovery after failure, JSON export download, and deleting generated results")
            ctx2.close()
        finally:
            env2.close()
            fake.close()
        browser.close()
finally:
    env.close()

print("\nsteps passed:", len(steps))
if problems:
    print("PROBLEMS:\n  " + "\n  ".join(problems))
    sys.exit(1)
print("no console errors, no CSP violations, no page errors")
