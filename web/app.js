"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const R = document.documentElement;
const LABEL = { draft: "Draft", analyzing: "Analyzing", plan_ready: "Plan ready", needs_attention: "Needs attention", ready_to_build: "Ready to build" };
const S = { user: null, pid: null, detail: null, latest: null, done: null, reqs: [], tab: "s", providers: null, poll: 0, tick: 0, tt: 0, cd: 0, list: 0 };
const fmt = s => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
const slug = s => (String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "solution").slice(0, 40);
const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} };
const first = () => (S.user.name || "there").split(" ")[0];

/* ---------------------------------------------------------------- preferences */
let prefs = { th: "system", mo: matchMedia("(prefers-reduced-motion:reduce)").matches ? "reduced" : "full" };
try { Object.assign(prefs, JSON.parse(ls("hs-prefs") || "{}")); } catch (e) {}
const mq = matchMedia("(prefers-color-scheme:dark)");
function applyPrefs() {
  R.dataset.theme = prefs.th === "system" ? (mq.matches ? "dark" : "light") : prefs.th;
  R.dataset.motion = prefs.mo;
  const t = $("#th"), m = $("#mo");
  if (t) { t.title = "Theme: " + prefs.th; t.setAttribute("aria-label", "Theme: " + prefs.th + ". Press to change"); }
  if (m) { m.title = "Motion: " + prefs.mo; m.setAttribute("aria-label", "Motion: " + prefs.mo + ". Press to change"); }
}
function setPref(k, v) { prefs[k] = v; ls("hs-prefs", JSON.stringify(prefs)); applyPrefs(); }
mq.addEventListener("change", applyPrefs);

/* ---------------------------------------------------------------- helpers */
function toast(m, bad) { const t = $("#toast"); t.textContent = m; t.className = "on" + (bad ? " bad" : ""); clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = ""), 3400); }
const errText = e => (e && e.details ? Object.values(e.details).join(" ") : (e && e.message) || "Something went wrong");
async function api(method, path, body) {
  const o = { method, credentials: "same-origin", headers: { "X-Requested-With": "HackSolve" } };
  if (body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
  let r;
  try { r = await fetch("/api" + path, o); } catch (e) { throw { code: "network", message: navigator.onLine ? "Can’t reach the server. Is it running?" : "You’re offline." }; }
  if (r.status === 204) return null;
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) {
    if (r.status === 401 && S.user && path !== "/auth/me") { S.user = null; go("#/login"); }
    throw j.error || { code: "error", message: "Something went wrong" };
  }
  return j.data;
}
async function copy(t) {
  try { await navigator.clipboard.writeText(t); } catch (e) { const a = document.createElement("textarea"); a.value = t; document.body.appendChild(a); a.select(); try { document.execCommand("copy"); } catch (_) {} a.remove(); }
}
function confirm2(t, msg) {
  if (t.dataset.c) return true;
  const o = t.textContent; t.dataset.c = 1; t.textContent = msg;
  setTimeout(() => { if (t.isConnected) { delete t.dataset.c; t.textContent = o; } }, 4000);
  return false;
}
const daysLeft = d => { const n = Math.ceil((new Date(d + "T23:59:00") - Date.now()) / 864e5); return n < 0 ? "passed" : n === 0 ? "today" : n + (n === 1 ? " day left" : " days left"); };
const badge = s => `<span class="badge ${s}">${LABEL[s] || s}</span>`;
function view(html, title) { $("#view").innerHTML = html; document.title = (title ? title + " · " : "") + "HackSolve"; }

/* ---------------------------------------------------------------- header + router */
function header(land) {
  const h = $("#hdr"); h.className = land ? "land" : "";
  h.innerHTML = `<div class="wrap bar"><a class="logo" href="#/"><i></i>HackSolve</a>
  <nav class="nav" aria-label="Sections"><button data-scroll="how">How it works</button><button data-scroll="faq">Questions</button></nav>
  <button class="btn ic" id="th">◐</button><button class="btn ic hide-s" id="mo">✦</button>
  ${S.user ? `<a class="btn" href="#/app">Dashboard</a><a class="btn ic av" href="#/settings" aria-label="Settings and account">${esc((S.user.name || "?")[0].toUpperCase())}</a>`
           : `<a class="btn hide-s" href="#/login">Log in</a><a class="btn p" href="#/signup">Start free</a>`}</div>`;
  applyPrefs();
}
const go = h => { if (location.hash === h) route(); else location.hash = h; };
function route() {
  [S.poll, S.tick, S.cd, S.list].forEach(clearInterval); S.tick = 0; S.tt = 0;
  const [a, b] = location.hash.replace(/^#\/?/, "").split("/");
  const land = !a;
  header(land);
  if (land) viewLanding();
  else if (a === "login" || a === "signup") S.user ? go("#/app") : viewAuth(a);
  else if (!S.user) go("#/login");
  else if (a === "app") viewDashboard();
  else if (a === "p" && /^[0-9a-f]{32}$/.test(b || "")) viewProject(b);
  else if (a === "settings") viewSettings();
  else go("#/app");
  if (!land || !location.hash) $("#main").focus({ preventScroll: true });
}

/* ---------------------------------------------------------------- landing */
function viewLanding() {
  view(`<div class="wrap hero"><div>
  <h1 class="rv" style="--i:0">Understand the brief. Plan the build. Win the demo.</h1>
  <p class="lead rv" style="--i:1">Drop in a hackathon problem statement as text, PDF or Word. HackSolve reads the rules, builds a working app that solves it, scans and sandbox-runs that app, then hands you the code, the proof and your pitch.</p>
  <div class="row rv" style="--i:2"><a class="btn p" href="#/signup">Start free</a><a class="btn" href="#/login">Log in</a></div></div>
  <div class="stage" id="stage" aria-hidden="true"><div class="si"><div class="sc" id="sc"><div class="glow"></div>
  <div class="c c1"><span class="mut">brief.pdf, 12 pages</span><div class="ln" style="width:92%"></div><div class="ln hl" style="width:78%"></div><div class="ln" style="width:86%"></div><div class="ln" style="width:60%"></div><div class="ln hl" style="width:70%"></div><div class="ln" style="width:90%"></div><div class="ln" style="width:48%"></div><div class="ln hl" style="width:64%"></div></div>
  <div class="c c2"><b>Requirements</b><br><span class="chip">Works offline</span><span class="chip">Explains alerts</span><span class="chip">Resets in one tap</span></div>
  <div class="c c3"><b>Building the app</b><div class="pg"><b style="width:68%"></b></div><span class="mut">Sandbox check passed</span></div>
  <div class="c c4"><b style="font:700 24px var(--se);color:var(--mint)">Guard ✓</b><br><span class="mut">Code scan</span></div></div></div></div></div>
  <div class="wrap sec" id="how"><h2>From wall of text to working demo</h2><div class="g">
  <div class="card"><b class="n">1</b><h4>Add the brief</h4><p class="mut">Paste text or drop a PDF, DOCX, TXT or MD file. Instruction-like text hidden inside is flagged and ignored.</p></div>
  <div class="card"><b class="n">2</b><h4>Read the rules</h4><p class="mut">Goal, constraints, deliverables, judging criteria, dates and testable requirements.</p></div>
  <div class="card"><b class="n">3</b><h4>Build and check</h4><p class="mut">A one-file app is written, scanned, syntax-checked and repaired once if it breaks.</p></div>
  <div class="card"><b class="n">4</b><h4>Demo and submit</h4><p class="mut">Live preview, code, pitch, judge Q&amp;A, rehearsal timer and a deadline checklist.</p></div></div>
  <p class="mut" style="margin-top:16px">Blue tags mark facts taken from your brief. Amber tags mark drafts. Judge feedback is always simulated.</p></div>
  <div class="wrap sec"><h2>Built to be trusted with your brief</h2><div class="g">
  <div class="card"><h4>Facts and suggestions, kept apart</h4><p class="mut">Every result says whether it came from your brief or was drafted for you.</p></div>
  <div class="card"><h4>Untrusted input stays untrusted</h4><p class="mut">Files and text are treated as data. Embedded commands are removed before analysis and logged.</p></div>
  <div class="card"><h4>Generated code is sandboxed</h4><p class="mut">Scanned first, then shown in a frame with no network and no access to your session.</p></div>
  <div class="card"><h4>Honest about its limits</h4><p class="mut">Demo mode says it isn’t an AI model. Live failures are reported, never hidden.</p></div></div></div>
  <div class="wrap sec" id="faq"><h2>Questions</h2>
  <details><summary>Does it really build the app?</summary><p class="mut">Yes, when an AI provider is configured on the server: Claude, Gemini, or any OpenAI-compatible model including free local ones through Ollama. Without one, the server runs in demo mode, extracts requirements and builds a requirements workbench, and labels it that way.</p></details>
  <details><summary>Where does my brief go?</summary><p class="mut">Into this server’s database and, if you pick a live provider, to that provider for analysis. Demo mode makes no external calls. Export or delete a project any time.</p></details>
  <details><summary>Is the generated code safe to run?</summary><p class="mut">It is scanned for unsafe code, syntax-checked and previewed in a sandboxed frame with no network access. Automated checks aren’t proof, so read it before you trust it.</p></details>
  <details><summary>Can it submit for me?</summary><p class="mut">No. It can’t verify official rules or submit on your behalf. Always check the organizer’s page.</p></details></div>
  <div class="wrap sec cta"><h2>Your next hackathon starts with a clearer brief.</h2><a class="btn p" href="#/signup">Start free</a></div>`, "");
  const g = $("#stage"), sc = $("#sc");
  g.addEventListener("pointermove", e => { if (prefs.mo === "reduced") return; const b = g.getBoundingClientRect(); sc.style.setProperty("--ry", -18 + ((e.clientX - b.left) / b.width - .5) * 34 + "deg"); sc.style.setProperty("--rx", 8 - ((e.clientY - b.top) / b.height - .5) * 26 + "deg"); });
  g.addEventListener("pointerleave", () => { sc.style.removeProperty("--ry"); sc.style.removeProperty("--rx"); });
}

/* ---------------------------------------------------------------- auth */
function viewAuth(mode) {
  const login = mode === "login";
  view(`<div class="auth"><div class="authform"><h1>${login ? "Welcome back" : "Create your account"}</h1>
  <p class="mut">${login ? "Log in to see your projects." : "Free to start. Your projects stay in your account."}</p>
  <form id="af" novalidate>
  ${login ? "" : '<label for="name">Name</label><input id="name" autocomplete="name" maxlength="80"><p class="err" id="e-name"></p>'}
  <label for="email">Email</label><input id="email" type="email" autocomplete="email"><p class="err" id="e-email"></p>
  <label for="password">Password</label><input id="password" type="password" autocomplete="${login ? "current-password" : "new-password"}"><p class="err" id="e-password"></p>
  ${login ? "" : '<div class="meter" id="meter" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div><p class="mut" id="mtxt">Use 10 or more characters and mix letters with numbers or symbols.</p>'}
  <div id="aerr"></div><button class="btn p" id="asub" style="width:100%;margin-top:12px">${login ? "Log in" : "Create account"}</button></form>
  <p class="mut" style="margin-top:20px">${login ? 'New here? <a href="#/signup">Create an account</a>' : 'Already have an account? <a href="#/login">Log in</a>'}</p>
  <div class="row"><button class="btn" disabled title="Not configured in this build">Google sign-in unavailable</button><button class="btn" disabled title="Not configured in this build">GitHub sign-in unavailable</button></div></div>
  <div class="story"><h2>Understand the brief. Plan the build. Win the demo.</h2>
  <ul><li>Reads PDF, Word, text and Markdown briefs.</li><li>Builds, scans and sandbox-runs a working app.</li><li>Keeps facts from your brief apart from AI drafts.</li></ul>
  <p class="mut">Built by students for hackathon weekends.</p></div></div>`, login ? "Log in" : "Create account");
  const pw = $("#password");
  if (!login) pw.addEventListener("input", () => {
    const v = pw.value, cls = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter(r => r.test(v)).length;
    const sc = (v.length >= 10) + (v.length >= 14) + (cls >= 2) + (cls >= 3) + (cls >= 4), m = $("#meter");
    $$("i", m).forEach((x, i) => x.classList.toggle("on", i < sc)); m.className = "meter" + (sc >= 4 ? " good" : "");
    $("#mtxt").textContent = !v ? "Use 10 or more characters and mix letters with numbers or symbols." : sc < 3 ? "Weak: add length and mix in numbers or symbols." : sc < 4 ? "Okay. A longer password is stronger." : "Strong.";
  });
}
async function submitAuth() {
  const login = location.hash === "#/login", b = $("#asub");
  $$(".err").forEach(x => (x.textContent = "")); $("#aerr").innerHTML = ""; b.disabled = true;
  const body = { email: $("#email").value.trim(), password: $("#password").value };
  if (!login) body.name = $("#name").value.trim();
  try { S.user = (await api("POST", "/auth/" + (login ? "login" : "register"), body)).user; toast(login ? "Welcome back" : "Account created"); go("#/app"); }
  catch (er) {
    for (const [k, v] of Object.entries(er.details || {})) { const el = $("#e-" + k); if (el) el.textContent = v; }
    if (!er.details) $("#aerr").innerHTML = `<div class="alert" role="alert">${esc(er.message)}</div>`;
  } finally { b.disabled = false; }
}

/* ---------------------------------------------------------------- dashboard */
function cover(id) {
  const h = parseInt(id.slice(0, 8), 16), c = ["var(--acc)", "var(--mint)"], r = i => (h >> (i * 3)) & 7;
  return `<svg class="cover" viewBox="0 0 240 112" aria-hidden="true" preserveAspectRatio="xMidYMid slice"><rect width="240" height="112" fill="var(--accs)"/>
  <rect x="${20 + r(0) * 8}" y="${14 + r(1) * 3}" width="86" height="64" rx="12" fill="${c[r(2) % 2]}" opacity=".9" transform="rotate(${r(3) * 2 - 7} 80 50)"/>
  <rect x="${112 + r(4) * 6}" y="${28 + r(5) * 3}" width="80" height="60" rx="12" fill="none" stroke="var(--ink)" stroke-width="2" transform="rotate(${r(6) * 2 - 7} 150 60)"/>
  <circle cx="${170 + r(7) * 6}" cy="${26 + r(1) * 3}" r="${12 + r(2)}" fill="var(--acc)" opacity=".28"/></svg>`;
}
const pcard = p => `<article class="card pc">${cover(p.id)}<div class="body"><div class="hd"><h3>${esc(p.title)}</h3>${badge(p.status)}</div>
  <div class="pg" role="progressbar" aria-label="Progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.progress}"><b style="width:${p.progress}%"></b></div>
  <div class="tags">${p.tags.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>
  <p class="mut" style="margin:0">${p.deadline ? "Deadline " + esc(p.deadline) + " (" + daysLeft(p.deadline) + ")" : "No deadline set"}</p>
  <div class="row" style="margin:auto 0 0"><a class="btn p s" href="#/p/${p.id}">${p.status === "draft" ? "Continue" : p.status === "analyzing" ? "Open" : "Review"}</a><button class="btn d s" data-act="delp" data-id="${p.id}">Delete</button></div></div></article>`;
async function loadList() {
  const d = await api("GET", "/projects"), box = $("#plist"); if (!box) return;
  box.innerHTML = d.projects.length ? `<div class="pgrid">${d.projects.map(pcard).join("")}</div>`
    : `<div class="empty"><h3>No projects yet</h3><p class="mut">Name your first project above, then paste or upload the hackathon brief. HackSolve does the rest.</p></div>`;
  clearInterval(S.list);
  if (d.projects.some(p => p.analysis && ["queued", "running"].includes(p.analysis.status))) S.list = setInterval(() => loadList().catch(() => {}), 2500);
}
async function viewDashboard() {
  view(`<div class="wrap sec"><h1 style="font-size:clamp(30px,4vw,44px)">Welcome back, ${esc(first())}</h1><p class="mut" style="margin-top:8px">Pick up where you left off, or start a new brief.</p>
  <form id="np" class="row"><input id="ntitle" placeholder="Name your project, e.g. Smart India Hackathon 2026" maxlength="120" aria-label="New project name" style="flex:1;min-width:220px;margin:0"><button class="btn p">New project</button></form><div id="plist"><p class="mut">Loading…</p></div></div>`, "Dashboard");
  try { await loadList(); } catch (e) { $("#plist").innerHTML = `<div class="alert" role="alert">${esc(errText(e))}</div>`; }
}

/* ---------------------------------------------------------------- project */
async function loadProject() {
  const [d, a] = await Promise.all([api("GET", "/projects/" + S.pid), api("GET", "/projects/" + S.pid + "/analysis")]);
  S.detail = d; S.latest = a.latest; S.done = a.completed; S.reqs = a.requirements;
}
const paintBadge = () => { const e = $("#pbadge"); if (e) e.innerHTML = badge(S.detail.project.status); };
async function refreshProject() { S.detail.project = (await api("GET", "/projects/" + S.pid)).project; paintBadge(); }
async function viewProject(pid) {
  if (S.pid !== pid) S.tab = "s";
  S.pid = pid;
  view('<div class="wrap sec"><p class="mut">Loading…</p></div>', "Project");
  try { await loadProject(); } catch (e) { toast(errText(e), 1); return go("#/app"); }
  if (!S.providers) { try { S.providers = await api("GET", "/providers"); } catch (e) { S.providers = { providers: [], default: "local" }; } }
  const p = S.detail.project;
  view(`<div class="wrap sec"><a class="back" href="#/app">All projects</a><div class="ph"><input class="ptitle" id="ptitle" value="${esc(p.title)}" aria-label="Project name" maxlength="120"><span id="pbadge"></span></div>
  <div class="card" id="briefcard"></div><div class="card" id="pipe" hidden></div><div class="card" id="res" hidden></div></div>`, p.title);
  paintBadge(); paintBrief(); paintPipe(); paintResults();
  if (S.latest && ["queued", "running"].includes(S.latest.status)) startPoll();
}
function paintFlags() {
  const f = (S.detail.brief && S.detail.brief.injection_flags) || [], e = $("#flags"); if (!e) return;
  e.innerHTML = f.length ? `<div class="warn" role="note"><b>${f.length} instruction-like line${f.length > 1 ? "s" : ""} found.</b> They will be ignored during analysis and logged. A brief is data, never commands.</div>` : "";
}
function paintBrief() {
  const p = S.detail.project, b = S.detail.brief, live = S.providers.providers.filter(x => x.id !== "local" && x.configured);
  $("#briefcard").innerHTML = `<h2>Brief</h2><label for="brief" style="margin-top:0">Problem statement</label>
  <textarea id="brief" placeholder="Paste the problem statement, rules and judging criteria…">${esc(b ? b.text : "")}</textarea><div id="flags"></div>
  <div class="two"><div><label class="drop" id="dz" tabindex="0">Drop a PDF, DOCX, TXT or MD file, or tap to choose<input id="file" type="file" accept=".pdf,.docx,.txt,.md" hidden></label><div class="pg" id="up" hidden><b style="width:0"></b></div><p class="mut" id="upmsg" style="margin:6px 0 0"></p></div>
  <div><label for="link" style="margin-top:0">Competition link (optional)</label><input id="link" type="url" placeholder="https://…" value="${esc(p.competition_url || "")}"><label for="dl">Deadline</label><input id="dl" type="date" value="${esc(p.deadline || "")}"></div></div>
  <div class="row"><select id="prov" aria-label="AI provider"><option value="auto">Auto (${S.providers.default === "local" ? "demo mode" : esc(S.providers.default || "not configured")})</option><option value="local">Demo mode (no AI model)</option>${live.map(x => `<option value="${x.id}">${esc(x.label)}</option>`).join("")}</select>
  <button class="btn p" id="goBtn" data-act="analyze">Analyze and build</button><button class="btn" id="stopBtn" data-act="stop" hidden>Stop</button></div>
  <p class="mut" style="margin:16px 0 0">${live.length ? "Live mode sends your brief, minus instruction-like lines, to the provider you pick." : "No AI provider is configured on this server, so analysis runs in demo mode (see Settings)."} Links are saved, never fetched. Everything else stays on this server.</p>`;
  paintFlags();
  const dz = $("#dz");
  dz.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file").click(); } };
  dz.ondragover = e => { e.preventDefault(); dz.classList.add("on"); }; dz.ondragleave = () => dz.classList.remove("on");
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove("on"); doUpload(e.dataTransfer.files[0]); };
}
function doUpload(file) {
  if (!file) return;
  const msg = $("#upmsg"), bar = $("#up"), fill = $("b", bar);
  if (!/\.(pdf|docx|txt|md)$/i.test(file.name)) return toast("Use a PDF, DOCX, TXT or MD file.", 1);
  if (file.size > 5 * 1048576) return toast("Files must be under 5 MB.", 1);
  bar.hidden = false; fill.style.width = "0%"; msg.textContent = "Uploading " + file.name + "…";
  const x = new XMLHttpRequest();
  x.open("POST", `/api/projects/${S.pid}/files`);
  x.setRequestHeader("X-Requested-With", "HackSolve"); x.setRequestHeader("Content-Type", "application/octet-stream"); x.setRequestHeader("X-Filename", encodeURIComponent(file.name));
  x.upload.onprogress = e => { if (e.lengthComputable) fill.style.width = Math.round(e.loaded / e.total * 100) + "%"; };
  x.onload = () => {
    let j = {}; try { j = JSON.parse(x.responseText); } catch (e) {}
    if (x.status < 300) {
      S.detail.brief = j.data.brief; $("#brief").value = j.data.brief.text; paintFlags(); fill.style.width = "100%";
      msg.textContent = "Read " + j.data.brief.chars.toLocaleString() + " characters from " + j.data.file.filename + (j.data.file.truncated ? ". The file is long, so only the first 60,000 characters were used" : "") + ". Review them, then analyze." + (j.data.file.warnings.length ? " " + j.data.file.warnings.join(" ") : ""); toast(j.data.file.truncated ? "File read (long file: first 60,000 characters used)" : "File read");
    } else { bar.hidden = true; msg.textContent = ""; toast((j.error && j.error.message) || "Upload failed", 1); }
  };
  x.onerror = () => { bar.hidden = true; toast("Can’t reach the server.", 1); };
  x.send(file);
}
async function analyze(regen) {
  const btn = $("#goBtn"); if (btn) btn.disabled = true;
  try {
    const ta = $("#brief"), text = ta.value.trim(), saved = S.detail.brief && S.detail.brief.text;
    if (text !== saved) {
      if (text.length < 20) throw { message: "Add a longer brief first (at least 20 characters)." };
      S.detail.brief = (await api("POST", `/projects/${S.pid}/brief`, { text })).brief; paintFlags();
    }
    const link = $("#link").value.trim() || null, dl = $("#dl").value || null, p = S.detail.project;
    if (link !== (p.competition_url || null) || dl !== (p.deadline || null)) S.detail.project = (await api("PATCH", `/projects/${S.pid}`, { competition_url: link, deadline: dl })).project;
    S.latest = (await api("POST", `/projects/${S.pid}/analyze`, { provider: $("#prov").value })).analysis;
    paintPipe(); paintBadge(); startPoll();
    $("#pipe").scrollIntoView({ behavior: prefs.mo === "reduced" ? "auto" : "smooth", block: "start" });
  } catch (e) { toast(errText(e), 1); if (btn) btn.disabled = false; }
}
function startPoll() {
  clearInterval(S.poll); const pid = S.pid;
  S.poll = setInterval(async () => {
    try {
      const d = await api("GET", `/projects/${pid}/analysis/status`); if (S.pid !== pid) return;
      S.latest = d.analysis; paintPipe();
      if (!["queued", "running"].includes(d.analysis.status)) {
        clearInterval(S.poll); await loadProject(); paintBadge(); paintPipe(); paintResults();
        const st = d.analysis.status; toast(st === "completed" ? "Analysis ready" : st === "failed" ? "Analysis failed. Your brief is safe." : "Analysis cancelled", st === "failed");
      }
    } catch (e) { /* a network blip: keep polling */ }
  }, 900);
}
function paintPipe() {
  const a = S.latest, box = $("#pipe"); if (!box) return;
  if (!a) { box.hidden = true; return; }
  box.hidden = false; const run = ["queued", "running"].includes(a.status);
  box.innerHTML = `<div class="hd"><h2>Pipeline</h2><span class="tag ${a.mode === "live" ? "y" : "s"}">${a.mode === "live" ? "Live AI: " + esc(a.model_used) : "Demo mode, no AI model"}</span></div>
  <div class="pg" role="progressbar" aria-label="Analysis progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${a.progress}"><b style="width:${a.progress}%"></b></div>
  <ol class="st">${a.stages.map(s => `<li class="${s.status}"><b>${esc(s.label)}</b><span>${esc(s.detail)}</span></li>`).join("")}</ol>
  ${["failed", "cancelled"].includes(a.status) ? `<div class="ban bad" role="alert" style="margin-top:16px">${esc(a.error_message || "")}</div><button class="btn" data-act="analyze">Try again</button>` : ""}`;
  const g = $("#goBtn"), s = $("#stopBtn"); if (g) g.disabled = run; if (s) s.hidden = !run;
}

/* ---------------------------------------------------------------- results */
const sec = (h, k, b) => `<div class="sb"><div class="hd"><h4>${h}</h4><span class="tag ${k ? "s" : ""}">${k ? (S.done.mode === "live" ? "AI draft" : "Rule-based draft") : "From your brief"}</span></div>${b}</div>`;
const li = (a, e = "Not stated in the brief.") => a && a.length ? "<ul>" + a.map(x => `<li>${esc(x)}</li>`).join("") + "</ul>" : `<p class="mut">${e}</p>`;
function paintResults() {
  const box = $("#res"); if (!box) return;
  if (!S.done) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<div class="tabs" role="tablist" aria-label="Results">${[["s", "Solution"], ["b", "Brief"], ["p", "Plan"], ["r", "Pitch and rehearsal"], ["c", "Code"], ["d", "Submit"]]
    .map(([k, n]) => `<button role="tab" class="btn tab" data-act="tab" data-k="${k}" aria-selected="${S.tab === k}">${n}</button>`).join("")}</div><div id="tabbody"></div>`;
  paintTab();
}
function paintTab() {
  clearInterval(S.cd);
  const d = S.done, r = d.result, ck = r.checks, body = $("#tabbody"), p = S.detail.project; let h = "";
  $$(".tab").forEach(t => t.setAttribute("aria-selected", t.dataset.k === S.tab));
  if (S.tab === "s") {
    h = `<div class="ban ${ck.ok ? "" : "bad"}">${ck.ok ? "Code Guard passed" + (ck.syntax.status === "ok" ? " and the JavaScript parses cleanly." : ".") + (ck.repaired ? " One automatic repair was needed." : "") : "Needs attention: " + esc(ck.guard[0] || ck.syntax.message) + ". Read the code before relying on it."}</div>
    ${d.mode === "demo" ? '<div class="ban bad">Demo mode built a requirements workbench, not a solution to your domain problem. Configure an AI provider on the server to build the real app.</div>' : ""}
    <iframe class="pv" sandbox="allow-scripts" title="Generated solution" src="/api/projects/${S.pid}/preview?v=${d.id}"></iframe>
    <p class="mut" id="runcheck" style="margin-top:12px">Running a sandbox check in your browser…</p><p class="mut">Automated checks can’t prove it’s correct. Click through every requirement before you demo.</p>`;
  } else if (S.tab === "b") {
    const rows = S.reqs.map(q => { const c = r.coverage && r.coverage[q.ref]; return `<li><label class="ck"><input type="checkbox" data-act="reqdone" data-id="${q.id}" ${q.status === "done" ? "checked" : ""}><span><b>${esc(q.ref)}</b> ${esc(q.title)} <span class="tag">${esc(q.category)}</span><br><span class="mut">${c ? "In the app: " + esc(c) : "Not marked in the code. Check it by hand."}</span></span></label></li>`; }).join("");
    h = `<p class="mut">Confidence ${Math.round((d.confidence || 0) * 100)}%. ${d.mode === "live" ? "AI extraction" : "Rule-based extraction, not an AI model"}; verify everything against the official rules.</p>
    ${sec("Primary goal", 0, `<p>${esc(r.goal || "Not stated in the brief.")}</p>`)}${sec("Target users", 0, li(r.users))}${sec("Constraints and rules", 0, li(r.constraints))}${sec("Deliverables", 0, li(r.deliverables))}${sec("Judging criteria", 0, li(r.judging))}
    ${sec("Deadline", 0, `<p>${esc(r.deadline || "Not stated in the brief. Confirm it with the organizers.")}</p>`)}${sec("Risks and unknowns", 1, li(r.risks, "None flagged."))}${sec("Requirements and where the app covers them", 1, `<ul style="list-style:none;padding:0">${rows}</ul>`)}`;
  } else if (S.tab === "p") {
    const pr = r.priorities, names = { must: "Must have", should: "Should have", could: "Could have", not_now: "Not now" };
    const j = r.judge || {}, bars = [["Clarity", "clarity"], ["Feasibility", "feasibility"], ["Impact", "impact"], ["Novelty", "novelty"], ["Demo readiness", "demo_readiness"]]
      .map(([n, k]) => `<div class="fb"><span>${n}</span><span class="pg"><b style="width:${d.mode === "demo" && k === "novelty" ? 0 : (j[k] || 0) * 20}%"></b></span><span>${d.mode === "demo" && k === "novelty" ? "not assessed" : (j[k] || 0) + "/5"}</span></div>`).join("");
    h = sec("Solution concept", 1, `<p>${esc(r.concept)}</p>`) + sec("Feature priorities", 1, Object.keys(names).map(k => `<h5>${names[k]}</h5>${li(pr[k], "Nothing listed.")}`).join("")) + sec("MVP scope", 1, `<p>${esc(r.mvp)}</p>`)
      + sec("Judge-ready explanation (editable draft)", 1, `<textarea id="expl" rows="6" aria-label="Explanation">${esc(d.explanation)}</textarea><p class="mut">Saves when you click away. Version ${d.explanation_version}.</p>`)
      + sec("Implementation prompt for an AI coding tool", 1, `<pre>${esc(d.implementation_prompt)}</pre><button class="btn p" data-act="copy" data-k="prompt">Copy prompt</button>`)
      + `<div class="sb"><div class="hd"><h4>Judge-style feedback</h4><span class="tag s">Simulated</span></div>${bars}<p class="mut">Heuristic scores, not a real judge.${d.mode === "demo" ? " Novelty needs real prior-art research." : ""}</p>${li(j.recommendations || [], "")}</div>`;
  } else if (S.tab === "r") {
    h = sec("30-second pitch", 1, `<p>${esc(r.pitch)}</p>`) + sec("Practice timer (3 minutes)", 1, `<div class="tm" id="tm">${fmt(S.tt || 180)}</div><div class="row" style="margin:0"><button class="btn p" id="tstart" data-act="tstart">${S.tick ? "Pause" : "Start"}</button><button class="btn" data-act="treset">Reset</button></div>`)
      + sec("Pitch structure", 1, li(["0:00 The problem and who feels it", "0:30 What I’m building, in one sentence", "1:00 Live demo of the happy path", "2:15 Why it’s feasible and different", "2:45 What’s next"]))
      + sec("Likely judge questions", 1, (r.qa || []).map((q, i) => `<label class="ck"><input type="checkbox" data-act="qa" data-i="${i}" ${ls("hs-qa-" + S.pid + "-" + i) ? "checked" : ""}><span><b>${esc(q.q)}</b><br><span class="mut">${esc(q.a)}</span></span></label>`).join(""))
      + `<div class="sb"><div class="hd"><h4>Your notes</h4><span class="tag y">Yours</span></div><textarea id="notes" rows="4" aria-label="Notes" placeholder="Talking points…">${esc(ls("hs-notes-" + S.pid) || "")}</textarea><p class="mut">Notes stay in this browser.</p></div>`;
  } else if (S.tab === "c") {
    h = `<pre>${esc(d.artifact.content)}</pre><div class="row" style="margin:0"><button class="btn p" data-act="copy" data-k="code">Copy code</button><button class="btn" data-act="download">Download .html</button><button class="btn" data-act="regen">Regenerate</button></div>`;
  } else {
    const it = S.detail.checklist, dn = it.filter(i => i.status === "done").length;
    h = `<div class="sb" style="border:0;padding-top:0"><div class="hd"><h4>Deadline countdown</h4><span class="tag y">Yours</span></div><div class="cd" id="cd"></div><label for="dl2" style="margin-top:0">Deadline</label><input id="dl2" type="date" value="${esc(p.deadline || "")}"></div>
    <div class="sb"><div class="hd"><h4>Submission checklist</h4><span class="tag y">Yours</span></div><div class="pg"><b id="cbar" style="width:${it.length ? dn / it.length * 100 : 0}%"></b></div>
    ${it.map(i => `<label class="ck"><input type="checkbox" data-act="chk" data-id="${i.id}" ${i.status === "done" ? "checked" : ""}><span>${esc(i.title)}<br><span class="mut">${esc(i.description)}</span></span></label>`).join("")}
    <form id="addc" class="row" style="margin-top:12px"><input id="ctitle" placeholder="Add your own item" maxlength="200" aria-label="New checklist item" style="flex:1;min-width:200px;margin:0"><button class="btn s">Add</button></form></div>
    <p class="warn">HackSolve can’t verify official rules or submit on your behalf. Check the organizer’s page before you submit.</p>
    <div class="row"><a class="btn" href="/api/projects/${S.pid}/export" download>Export project JSON</a><button class="btn d" data-act="delart">Delete generated results</button><button class="btn d" data-act="delproj">Delete project</button></div>`;
  }
  body.innerHTML = h;
  if (S.tab === "s") probe();
  if (S.tab === "d") { const u = () => { const c = $("#cd"); if (!c) return; const t = p.deadline ? new Date(p.deadline + "T23:59:00") - Date.now() : NaN; c.textContent = isNaN(t) ? "Set a deadline" : t < 0 ? "Deadline passed" : Math.floor(t / 864e5) + "d " + Math.floor(t % 864e5 / 36e5) + "h " + Math.floor(t % 36e5 / 6e4) + "m " + Math.floor(t % 6e4 / 1e3) + "s"; }; u(); S.cd = setInterval(u, 1000); }
}
function probe() {
  const f = document.createElement("iframe"), errs = []; let len = null;
  f.setAttribute("sandbox", "allow-scripts"); f.title = "Sandbox check"; f.style.cssText = "position:fixed;left:-9999px;width:900px;height:700px;border:0";
  const on = e => { if (e.source !== f.contentWindow) return; const d = e.data || {}; if (d.hsErr) errs.push(String(d.hsErr).slice(0, 140)); if (d.hsLen != null) len = d.hsLen; };
  addEventListener("message", on); f.src = `/api/projects/${S.pid}/preview?probe=1&v=${S.done.id}`; document.body.appendChild(f);
  setTimeout(() => { removeEventListener("message", on); f.remove(); const e = $("#runcheck"); if (e) e.textContent = errs.length ? "Browser check found errors: " + errs[0] : len === null ? "The browser check couldn’t load the app." : len < 40 ? "The app rendered almost nothing." : "Ran in your browser without errors."; }, 2800);
}

/* ---------------------------------------------------------------- settings */
async function viewSettings() {
  let pr; try { pr = await api("GET", "/providers"); } catch (e) { pr = { providers: [] }; }
  view(`<div class="wrap sec"><h1 style="font-size:clamp(30px,4vw,44px);margin-bottom:24px">Settings</h1>
  <div class="card"><h3>Appearance</h3><label for="sth" style="margin-top:0">Theme</label><select id="sth"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select>
  <label for="smo">Motion</label><select id="smo"><option value="full">Full motion</option><option value="reduced">Reduced motion</option></select></div>
  <div class="card"><h3>AI provider status</h3>${pr.providers.map(x => `<div class="hd"><span>${esc(x.label)} <span class="mut">${esc(x.model)}</span></span><span class="tag ${x.configured ? "y" : "s"}">${x.configured ? "Configured" : "Not configured"}</span></div>`).join("")}
  <p class="mut">Keys live in the server’s .env file and never reach the browser. Add one and restart the server to switch from demo mode to live AI.</p></div>
  <div class="card"><h3>Privacy and data retention</h3><p class="mut">Briefs, uploads and generated apps are stored in this server’s database and uploads folder. In live mode the brief text, minus instruction-like lines, is sent to the provider you choose. Demo mode makes no external calls. Deleting a project removes its brief, uploaded files and results. Sessions expire automatically.</p></div>
  <div class="card"><h3>Account</h3><p>${esc(S.user.name)} <span class="mut">${esc(S.user.email)}</span></p><button class="btn" data-act="logout">Log out</button></div></div>`, "Settings");
  $("#sth").value = prefs.th; $("#smo").value = prefs.mo;
}

/* ---------------------------------------------------------------- actions */
const ACT = {
  analyze: () => analyze(),
  stop: async () => { await api("POST", `/projects/${S.pid}/analysis/cancel`); toast("Stopping after the current step"); },
  tab: t => { S.tab = t.dataset.k; paintTab(); },
  copy: async t => { await copy(t.dataset.k === "prompt" ? S.done.implementation_prompt : S.done.artifact.content); toast(t.dataset.k === "prompt" ? "Prompt copied" : "Code copied"); },
  download: () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([S.done.artifact.content], { type: "text/html" })); a.download = slug(S.detail.project.title) + ".html"; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); },
  regen: async () => { S.latest = (await api("POST", `/analysis/${S.done.id}/regenerate`, { provider: ($("#prov") || {}).value || "auto" })).analysis; paintPipe(); paintBadge(); startPoll(); $("#pipe").scrollIntoView({ block: "start" }); },
  delp: async t => { if (!confirm2(t, "Confirm delete")) return; await api("DELETE", "/projects/" + t.dataset.id); toast("Project deleted"); await loadList(); },
  delproj: async t => { if (!confirm2(t, "Tap again to delete the project")) return; await api("DELETE", "/projects/" + S.pid); toast("Project deleted"); go("#/app"); },
  delart: async t => { if (!confirm2(t, "Tap again to delete results")) return; await api("DELETE", `/projects/${S.pid}/artifacts`); await loadProject(); paintBadge(); paintPipe(); paintResults(); toast("Generated results deleted"); },
  tstart: t => {
    if (S.tick) { clearInterval(S.tick); S.tick = 0; t.textContent = "Start"; return; }
    S.tt = S.tt || 180; t.textContent = "Pause";
    S.tick = setInterval(() => { S.tt--; const m = $("#tm"); if (m) m.textContent = fmt(Math.max(S.tt, 0)); if (S.tt <= 0) { clearInterval(S.tick); S.tick = 0; const b = $("#tstart"); if (b) b.textContent = "Start"; toast("Time’s up"); } }, 1000);
  },
  treset: () => { clearInterval(S.tick); S.tick = 0; S.tt = 0; const m = $("#tm"), b = $("#tstart"); if (m) m.textContent = "3:00"; if (b) b.textContent = "Start"; },
  logout: async () => { await api("POST", "/auth/logout"); S.user = null; S.pid = null; toast("Logged out"); go("#/"); },
};
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-act],[data-scroll],#th,#mo,#skip"); if (!t) return;
  if (t.id === "th") return setPref("th", { system: "light", light: "dark", dark: "system" }[prefs.th]);
  if (t.id === "mo") return setPref("mo", prefs.mo === "full" ? "reduced" : "full");
  if (t.id === "skip") { e.preventDefault(); return $("#main").focus(); }
  if (t.dataset.scroll) { const el = document.getElementById(t.dataset.scroll); return el && el.scrollIntoView({ behavior: prefs.mo === "reduced" ? "auto" : "smooth" }); }
  const fn = ACT[t.dataset.act]; if (fn) try { await fn(t, e); } catch (er) { toast(errText(er), 1); }
});
document.addEventListener("change", async e => {
  const t = e.target, a = t.dataset.act;
  try {
    if (a === "chk") { await api("PATCH", "/checklist/" + t.dataset.id, { status: t.checked ? "done" : "todo" }); const i = S.detail.checklist.find(x => x.id === t.dataset.id); if (i) i.status = t.checked ? "done" : "todo"; const bx = $$("[data-act=chk]"); $("#cbar").style.width = bx.filter(x => x.checked).length / bx.length * 100 + "%"; await refreshProject(); }
    else if (a === "reqdone") { await api("PATCH", "/analysis/" + S.done.id, { requirements: [{ id: t.dataset.id, status: t.checked ? "done" : "open" }] }); const q = S.reqs.find(x => x.id === t.dataset.id); if (q) q.status = t.checked ? "done" : "open"; }
    else if (a === "qa") ls("hs-qa-" + S.pid + "-" + t.dataset.i, t.checked ? "1" : null);
    else if (t.id === "file") { doUpload(t.files[0]); t.value = ""; }
    else if (t.id === "dl2") { S.detail.project = (await api("PATCH", `/projects/${S.pid}`, { deadline: t.value || null })).project; paintTab(); toast("Deadline saved"); }
    else if (t.id === "ptitle") { const v = t.value.trim(); if (v) { S.detail.project = (await api("PATCH", `/projects/${S.pid}`, { title: v })).project; document.title = v + " · HackSolve"; toast("Saved"); } }
    else if (t.id === "sth") setPref("th", t.value);
    else if (t.id === "smo") setPref("mo", t.value);
  } catch (er) { toast(errText(er), 1); if (t.type === "checkbox") t.checked = !t.checked; }
});
document.addEventListener("focusout", async e => {
  if (e.target.id === "expl" && S.done && e.target.value !== S.done.explanation) {
    try { const r = await api("PATCH", "/analysis/" + S.done.id, { explanation: e.target.value }); S.done.explanation = r.explanation; S.done.explanation_version = r.explanation_version; toast("Explanation saved"); } catch (er) { toast(errText(er), 1); }
  }
});
document.addEventListener("input", e => { if (e.target.id === "notes") ls("hs-notes-" + S.pid, e.target.value); });
document.addEventListener("submit", async e => {
  e.preventDefault(); const id = e.target.id;
  try {
    if (id === "af") await submitAuth();
    else if (id === "np") { const v = $("#ntitle").value.trim(); if (!v) return toast("Name your project first", 1); const d = await api("POST", "/projects", { title: v }); go("#/p/" + d.project.id); }
    else if (id === "addc") { const v = $("#ctitle").value.trim(); if (!v) return; const d = await api("POST", `/projects/${S.pid}/checklist`, { title: v }); S.detail.checklist.push(d.item); await refreshProject(); paintTab(); toast("Item added"); }
  } catch (er) { toast(errText(er), 1); }
});
(async () => {
  applyPrefs();
  addEventListener("hashchange", route);
  addEventListener("offline", () => toast("You’re offline. Changes can’t be saved.", 1));
  addEventListener("online", () => toast("Back online"));
  const landing = () => !location.hash || location.hash === "#/";
  const who = api("GET", "/auth/me").then(d => { S.user = d.user; }).catch(() => { S.user = null; });
  const waiting = !landing();                   // only protected/auth pages must wait for the session check
  if (!waiting) route();                        // first paint of the landing page never waits on the network
  else { header(false); view('<div class="wrap sec"><p class="mut">Loading…</p></div>'); }
  await who;
  if (waiting) route();
  else { header(landing()); if (S.user && /^#\/(login|signup)/.test(location.hash)) go("#/app"); }
})();
