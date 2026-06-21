// NowPlix support — admin dashboard SPA. Vanilla ES module, no build step.
// All DOM classes are prefixed `npadmin-` to avoid host-page collisions.

const TOKEN_KEY = "npadmin_token";
const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || null,
  view: "overview",
  from: isoDaysAgo(30),
  to: isoToday(),
  // Supported languages (loaded once from /admin/meta) for the dropdowns.
  languages: null,
  defaultLang: "ru",
};

function isoToday() { return new Date().toISOString().slice(0, 10); }
function isoDaysAgo(n) {
  const d = new Date(); d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, { method = "GET", body = null, raw = false, form = null } = {}) {
  const headers = {};
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  let payload;
  if (form) { payload = form; }
  else if (body) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(`/admin${path}`, { method, headers, body: payload });
  if (res.status === 401) { logout(); throw new Error("unauthorized"); }
  if (raw) return { ok: res.ok, status: res.status, text: await res.text() };
  let data = null;
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

function q() { return `?from=${state.from}&to=${state.to}`; }

// Load the supported-language list once (for the language dropdowns). Falls
// back to a sane default set if the meta call fails so the UI still works.
async function ensureMeta() {
  if (state.languages) return;
  try {
    const m = await api("/meta");
    state.languages = m.languages || [];
    if (m.default_language) state.defaultLang = m.default_language;
  } catch (_) {
    state.languages = [
      { code: "en", name: "English" }, { code: "es", name: "Spanish" },
      { code: "ru", name: "Russian" }, { code: "tr", name: "Turkish" },
      { code: "pt", name: "Portuguese" },
    ];
  }
}

// Build a <select> of supported languages, defaulting to `selected` (or the
// service default). Replaces the old free-text language inputs.
function langSelect(selected) {
  const sel = el("select", "npadmin-input");
  sel.style.width = "auto";
  const want = (selected || state.defaultLang || "ru").toLowerCase();
  for (const l of (state.languages || [])) {
    const o = el("option", null, `${l.name} (${l.code})`);
    o.value = l.code;
    if (l.code === want) o.selected = true;
    sel.appendChild(o);
  }
  return sel;
}

// ---------------------------------------------------------------------------
// auth
// ---------------------------------------------------------------------------
function logout() {
  state.token = null;
  sessionStorage.removeItem(TOKEN_KEY);
  renderLogin();
}

function renderLogin() {
  const root = document.getElementById("npadmin-root");
  root.innerHTML = "";
  const box = el("div", "npadmin-login");
  box.appendChild(el("h1", null, "NowPlix Support — Admin"));
  const inp = el("input", "npadmin-input");
  inp.type = "password"; inp.placeholder = "Admin password";
  const btn = el("button", "npadmin-btn", "Sign in");
  btn.style.marginTop = "12px";
  const err = el("div", "npadmin-err");
  async function doLogin() {
    err.textContent = "";
    try {
      const res = await fetch("/admin/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: inp.value }),
      });
      const data = await res.json();
      if (!res.ok) { err.textContent = data.detail || "Login failed"; return; }
      state.token = data.token;
      sessionStorage.setItem(TOKEN_KEY, data.token);
      renderApp();
    } catch (e) { err.textContent = "Network error"; }
  }
  btn.addEventListener("click", doLogin);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  box.append(inp, btn, err);
  root.appendChild(box);
  inp.focus();
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------
const VIEWS = [
  ["overview", "Overview"], ["sessions", "Sessions"], ["unresolved", "Unresolved"],
  ["kb", "Knowledge base"], ["prompt", "Prompt"], ["settings", "Settings"],
];

function renderApp() {
  const root = document.getElementById("npadmin-root");
  root.innerHTML = "";
  const app = el("div", "npadmin-app");
  const side = el("div", "npadmin-side");
  side.appendChild(el("div", "npadmin-brand", "NowPlix Admin"));
  const nav = el("div", "npadmin-nav");
  for (const [id, label] of VIEWS) {
    const b = el("button", id === state.view ? "active" : null, label);
    b.addEventListener("click", () => { state.view = id; renderApp(); });
    nav.appendChild(b);
  }
  side.appendChild(nav);
  const out = el("button", "npadmin-btn ghost npadmin-logout", "Sign out");
  out.addEventListener("click", logout);
  side.appendChild(out);

  const main = el("div", "npadmin-main");
  main.id = "npadmin-main";
  app.append(side, main);
  root.appendChild(app);
  routeView(main);
}

function dateToolbar(onChange) {
  const bar = el("div", "npadmin-toolbar");
  const f = el("input", "npadmin-input"); f.type = "date"; f.value = state.from;
  f.style.width = "auto";
  const t = el("input", "npadmin-input"); t.type = "date"; t.value = state.to;
  t.style.width = "auto";
  f.addEventListener("change", () => { state.from = f.value; onChange(); });
  t.addEventListener("change", () => { state.to = t.value; onChange(); });
  bar.append(el("span", "npadmin-meta", "From"), f,
             el("span", "npadmin-meta", "To"), t);
  return bar;
}

function routeView(main) {
  main.innerHTML = "";
  const map = {
    overview: viewOverview, sessions: viewSessions, unresolved: viewUnresolved,
    kb: viewKB, prompt: viewPrompt, settings: viewSettings,
  };
  (map[state.view] || viewOverview)(main);
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
async function viewOverview(main) {
  main.appendChild(el("h1", "npadmin-h", "Overview"));
  main.appendChild(dateToolbar(() => routeView(main)));
  const cards = el("div", "npadmin-cards"); main.appendChild(cards);
  cards.appendChild(el("div", "npadmin-meta", "Loading…"));
  try {
    const o = await api(`/overview${q()}`);
    cards.innerHTML = "";
    const fmtPct = (v) => `${(v * 100).toFixed(1)}%`;
    const fmtUsd = (v) => `$${Number(v).toFixed(4)}`;
    card(cards, o.sessions_total, "Sessions (total)");
    card(cards, o.sessions_engaged, "Engaged (≥1 msg)");
    card(cards, fmtPct(o.escalation_rate), "Escalation rate");
    card(cards, fmtPct(o.resolution_rate), "Resolution rate", "proxy — incl. abandoned");
    card(cards, o.sessions_open, "Open (abandonment)");
    card(cards, fmtUsd(o.cost_usd_total), "Cost total");
    card(cards, fmtUsd(o.cost_usd_per_session), "Cost / session");
    card(cards, fmtPct(o.cache_hit_ratio), "Cache-hit ratio");
    card(cards, o.avg_messages_per_session, "Avg msgs / session");
    card(cards, o.failovers, "Key failovers");
    card(cards, o.rate_limit_blocks, "Rate-limit blocks");
    card(cards, o.injection_blocks, "Injection blocks");

    await chartFor(main, "sessions", "Sessions over time");
    await chartFor(main, "cost", "Cost over time (USD)");
    await chartFor(main, "escalation_rate", "Escalation rate over time");

    await tableByTopic(main);
    await tableByLanguage(main);
    await tableAB(main);
  } catch (e) { cards.innerHTML = ""; cards.appendChild(errBox(e)); }
}

function card(parent, value, label, note) {
  const c = el("div", "npadmin-card");
  c.appendChild(el("div", "v", String(value)));
  c.appendChild(el("div", "l", label));
  if (note) c.appendChild(el("div", "npadmin-proxy", note));
  parent.appendChild(c);
}

async function chartFor(main, metric, title) {
  const wrap = el("div", "npadmin-chart");
  wrap.appendChild(el("div", "npadmin-meta", title));
  main.appendChild(wrap);
  const data = await api(`/timeseries${q()}&metric=${metric}&bucket=day`);
  wrap.appendChild(lineChart(data.series));
}

// Hand-rolled inline SVG line chart (no external charting dependency).
function lineChart(series) {
  const W = 800, H = 160, pad = 24;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!series || !series.length) {
    const t = document.createElementNS(svg.namespaceURI, "text");
    t.setAttribute("x", 12); t.setAttribute("y", 24); t.setAttribute("fill", "#9aa7c2");
    t.textContent = "No data in range"; svg.appendChild(t); return svg;
  }
  const vals = series.map((d) => d.value);
  const max = Math.max(...vals, 0.0001);
  const stepX = (W - pad * 2) / Math.max(series.length - 1, 1);
  const pts = series.map((d, i) => {
    const x = pad + i * stepX;
    const y = H - pad - (d.value / max) * (H - pad * 2);
    return [x, y];
  });
  const path = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const line = document.createElementNS(svg.namespaceURI, "path");
  line.setAttribute("d", path); line.setAttribute("fill", "none");
  line.setAttribute("stroke", "#4f8cff"); line.setAttribute("stroke-width", "2");
  svg.appendChild(line);
  for (const [x, y] of pts) {
    const c = document.createElementNS(svg.namespaceURI, "circle");
    c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", "2.5");
    c.setAttribute("fill", "#4f8cff"); svg.appendChild(c);
  }
  const mx = document.createElementNS(svg.namespaceURI, "text");
  mx.setAttribute("x", 4); mx.setAttribute("y", 14); mx.setAttribute("fill", "#9aa7c2");
  mx.setAttribute("font-size", "11"); mx.textContent = `max ${max.toFixed(2)}`;
  svg.appendChild(mx);
  return svg;
}

async function tableByTopic(main) {
  const data = await api(`/by-topic${q()}`);
  const t = table(["Topic", "Sessions", "Escalation rate", "Avg msgs"]);
  for (const r of data.topics) {
    addRow(t, [r.slug, r.sessions, pct(r.escalation_rate), r.avg_messages.toFixed(1)]);
  }
  section(main, "By topic", t);
}

async function tableByLanguage(main) {
  const data = await api(`/by-language${q()}`);
  const t = table(["Language", "Sessions", "Escalation rate"]);
  for (const r of data.languages) addRow(t, [r.lang, r.sessions, pct(r.escalation_rate)]);
  section(main, "By language", t);
}

async function tableAB(main) {
  const data = await api(`/ab/results${q()}`);
  if (!data.results.length) return;
  const t = table(["Version", "Sessions", "Escalation", "Resolution", "Avg msgs", "Avg cost"]);
  for (const r of data.results) {
    addRow(t, [r.version_name || r.version_id, r.sessions, pct(r.escalation_rate),
               pct(r.resolution_rate), r.avg_messages.toFixed(1), `$${r.avg_cost.toFixed(5)}`]);
  }
  section(main, "A/B results", t);
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------
async function viewSessions(main) {
  main.appendChild(el("h1", "npadmin-h", "Sessions"));
  const bar = dateToolbar(() => routeView(main));
  const search = el("input", "npadmin-input"); search.placeholder = "Search text…";
  search.style.width = "auto";
  const escSel = el("select", "npadmin-input"); escSel.style.width = "auto";
  for (const [v, l] of [["", "All"], ["true", "Escalated"], ["false", "Not escalated"]]) {
    const o = el("option", null, l); o.value = v; escSel.appendChild(o);
  }
  const go = el("button", "npadmin-btn", "Filter");
  bar.append(search, escSel, go);
  main.appendChild(bar);
  const holder = el("div"); main.appendChild(holder);

  async function load(page = 1) {
    holder.innerHTML = "Loading…";
    let url = `/sessions${q()}&page=${page}`;
    if (search.value) url += `&q=${encodeURIComponent(search.value)}`;
    if (escSel.value) url += `&escalated=${escSel.value}`;
    try {
      const data = await api(url);
      holder.innerHTML = "";
      const t = table(["Created", "Topic", "Lang", "Status", "Msgs", ""]);
      for (const s of data.items) {
        const tr = addRow(t, [s.created_at.slice(0, 16).replace("T", " "),
          s.topic || "—", s.lang || "—",
          s.escalated ? "escalated" : s.status, s.message_count, "view →"]);
        tr.classList.add("click");
        tr.addEventListener("click", () => openSession(s.id));
      }
      holder.appendChild(t);
      holder.appendChild(el("div", "npadmin-meta",
        `${data.total} sessions — page ${data.page}`));
      const pager = el("div", "npadmin-toolbar");
      if (page > 1) { const p = el("button", "npadmin-btn ghost", "Prev");
        p.addEventListener("click", () => load(page - 1)); pager.appendChild(p); }
      if (page * data.page_size < data.total) { const n = el("button", "npadmin-btn ghost", "Next");
        n.addEventListener("click", () => load(page + 1)); pager.appendChild(n); }
      holder.appendChild(pager);
    } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
  }
  go.addEventListener("click", () => load(1));
  load(1);
}

async function openSession(id) {
  const main = document.getElementById("npadmin-main");
  main.innerHTML = "Loading…";
  try {
    const d = await api(`/session/${id}`);
    main.innerHTML = "";
    const back = el("button", "npadmin-btn ghost", "← Back");
    back.addEventListener("click", () => routeView(main));
    main.appendChild(back);
    main.appendChild(el("h1", "npadmin-h", "Session " + id.slice(0, 8)));
    const meta = el("div", "npadmin-meta");
    meta.textContent = `status=${d.session.status} · escalated=${d.session.escalated}`
      + ` · lang=${d.session.lang || "—"} · cost=$${d.cost_usd_total}`
      + ` · prompt_version=${d.session.prompt_version_id || "—"}`;
    main.appendChild(meta);

    const row = el("div", "npadmin-row");
    const convo = el("div", "npadmin-col");
    for (const m of d.messages) {
      const bubble = el("div", `npadmin-msg ${m.role}`, m.content);
      if (m.role === "assistant" && (m.cost_usd != null || m.key_used)) {
        bubble.appendChild(el("div", "npadmin-meta",
          `${m.model || ""} · key=${m.key_used || "—"} · $${m.cost_usd ?? 0}`));
      }
      convo.appendChild(bubble);
    }
    const ctx = el("div", "npadmin-col");
    ctx.appendChild(el("div", "npadmin-meta", "user_context"));
    const pre = el("pre", "npadmin-input");
    pre.textContent = JSON.stringify(d.session.user_context, null, 2);
    pre.style.whiteSpace = "pre-wrap";
    ctx.appendChild(pre);
    row.append(convo, ctx);
    main.appendChild(row);
  } catch (e) { main.innerHTML = ""; main.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Unresolved
// ---------------------------------------------------------------------------
async function viewUnresolved(main) {
  main.appendChild(el("h1", "npadmin-h", "Unresolved queries"));
  main.appendChild(el("div", "npadmin-help",
    "Escalated/unresolved sessions grouped by topic — read clusters and grow the KB."));
  const bar = dateToolbar(() => routeView(main));
  const exp = el("button", "npadmin-btn ghost", "Export CSV");
  exp.addEventListener("click", async () => {
    const r = await api(`/unresolved${q()}&format=csv`, { raw: true });
    const blob = new Blob([r.text], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "unresolved.csv"; a.click();
  });
  bar.append(exp);
  main.appendChild(bar);
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api(`/unresolved${q()}`);
    holder.innerHTML = "";
    for (const g of data.groups) {
      holder.appendChild(el("h3", null, `${g.topic} (${g.count})`));
      const t = table(["First message", "Msgs", "Session"]);
      for (const s of g.sessions) {
        const tr = addRow(t, [s.first_message || "—", s.message_count, s.session_id.slice(0, 8)]);
        tr.classList.add("click");
        tr.addEventListener("click", () => openSession(s.session_id));
      }
      holder.appendChild(t);
    }
    if (!data.groups.length) holder.appendChild(el("div", "npadmin-meta", "Nothing unresolved 🎉"));
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Knowledge base
// ---------------------------------------------------------------------------
async function viewKB(main) {
  main.appendChild(el("h1", "npadmin-h", "Knowledge base"));
  main.appendChild(el("div", "npadmin-help",
    "These are the live knowledge-base entries injected per topic into the prompt. "
    + "The default Russian KB ships seeded — view, edit or extend it below."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    await ensureMeta();
    const data = await api("/kb/topics");
    holder.innerHTML = "";

    // import block
    const imp = el("div", "npadmin-chart");
    imp.appendChild(el("div", "npadmin-meta", "Bulk import (JSON / CSV / Markdown)"));
    const file = el("input", "npadmin-input"); file.type = "file"; file.style.width = "auto";
    const fmt = el("select", "npadmin-input"); fmt.style.width = "auto";
    for (const f of ["json", "csv", "markdown"]) { const o = el("option", null, f); o.value = f; fmt.appendChild(o); }
    const lang = langSelect("ru");
    const up = el("button", "npadmin-btn", "Import");
    const impErr = el("span", "npadmin-meta");
    up.addEventListener("click", async () => {
      if (!file.files[0]) return;
      const fd = new FormData();
      fd.append("file", file.files[0]); fd.append("format", fmt.value); fd.append("lang", lang.value);
      try {
        const r = await api("/kb/import", { method: "POST", form: fd });
        impErr.textContent = `Imported ${r.inserted}; skipped ${r.skipped_unknown_topics.join(", ") || "none"}`;
        viewKB(main);
      } catch (e) { impErr.textContent = e.message; }
    });
    imp.append(el("div", "npadmin-toolbar", ""));
    const row = el("div", "npadmin-toolbar"); row.append(file, fmt, el("span", "npadmin-meta", "lang"), lang, up, impErr);
    imp.appendChild(row);
    holder.appendChild(imp);

    for (const topic of data.topics) {
      const tt = topic.title.en || topic.title.ru || topic.slug;
      holder.appendChild(el("h3", null, `${tt} — ${topic.slug} (${topic.entry_count} entries)`));
      const editor = el("div", "npadmin-row");
      const ta = el("textarea", "npadmin-input");
      ta.placeholder = "New entry content…";
      const langSel = langSelect("ru");
      const save = el("button", "npadmin-btn", "Add entry");
      save.addEventListener("click", async () => {
        if (!ta.value.trim()) return;
        await api("/kb/entries", { method: "POST",
          body: { topic_id: topic.id, lang: langSel.value, content: ta.value } });
        viewKB(main);
      });
      const left = el("div", "npadmin-col"); left.append(ta);
      const right = el("div", "npadmin-col");
      right.append(el("div", "npadmin-meta", "lang"), langSel, save);
      editor.append(left, right);
      holder.appendChild(editor);

      // list existing entries with inline edit + delete
      const entries = await api(`/kb/entries?topic_id=${topic.id}`);
      const t = table(["Lang", "Ver", "Content", "Actions"]);
      for (const e of entries.entries) {
        const actions = el("td");
        const editBtn = el("button", "npadmin-btn ghost", "Edit");
        const delBtn = el("button", "npadmin-btn ghost", "Delete");
        editBtn.addEventListener("click", () => openKBEntryEditor(main, e));
        delBtn.addEventListener("click", async () => {
          if (!confirm("Delete this KB entry?")) return;
          await api(`/kb/entries/${e.id}`, { method: "DELETE" }); viewKB(main);
        });
        actions.append(editBtn, delBtn);
        const tr = rowEls(t, [e.lang, e.version, e.content.slice(0, 120)]);
        tr.appendChild(actions);
      }
      holder.appendChild(t);
    }
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// Full-screen-ish inline editor for a single KB entry. Editing creates a new
// version row server-side (PUT /kb/entries/{id}); the old version is superseded.
function openKBEntryEditor(main, entry) {
  main.innerHTML = "";
  const back = el("button", "npadmin-btn ghost", "← Back to KB");
  back.addEventListener("click", () => routeView(main));
  main.appendChild(back);
  main.appendChild(el("h1", "npadmin-h", `Edit KB entry #${entry.id} (${entry.lang})`));
  const ta = el("textarea", "npadmin-input");
  ta.value = entry.content;
  ta.style.minHeight = "320px"; ta.style.width = "100%";
  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn", "Save (new version)");
  save.addEventListener("click", async () => {
    err.textContent = "";
    if (!ta.value.trim()) { err.textContent = "Content cannot be empty"; return; }
    try {
      await api(`/kb/entries/${entry.id}`, { method: "PUT", body: { content: ta.value } });
      routeView(main);
    } catch (e) { err.textContent = e.message; }
  });
  main.append(ta, save, err);
}

// ---------------------------------------------------------------------------
// Prompt versioning + A/B
// ---------------------------------------------------------------------------
async function viewPrompt(main) {
  main.appendChild(el("h1", "npadmin-h", "System prompt"));
  main.appendChild(el("div", "npadmin-help",
    "The base system prompt (Russian) is shipped seeded as the live default. "
    + "Published versions are immutable — to change the live prompt, edit it into "
    + "a new draft and publish that draft."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api("/prompts");
    holder.innerHTML = "";

    // live default (base) prompt — shown so the owner can see/edit what is active
    const live = data.versions.find((v) => v.is_default);
    if (live) {
      const cur = el("div", "npadmin-chart");
      cur.appendChild(el("div", "npadmin-meta",
        `Live default prompt — ${live.name} (#${live.id})`));
      const view = el("textarea", "npadmin-input");
      view.value = live.body || ""; view.readOnly = true;
      view.style.minHeight = "220px"; view.style.width = "100%";
      cur.appendChild(view);
      const edit = el("button", "npadmin-btn", "Edit as new draft");
      edit.addEventListener("click", () => openPromptEditor(main, live, true));
      cur.appendChild(edit);
      holder.appendChild(cur);
    }

    // new draft (from scratch)
    const draft = el("div", "npadmin-chart");
    draft.appendChild(el("div", "npadmin-meta", "Create draft from scratch"));
    const name = el("input", "npadmin-input"); name.placeholder = "Version name";
    const bodyTa = el("textarea", "npadmin-input"); bodyTa.placeholder = "Core prompt body (Russian)…";
    const create = el("button", "npadmin-btn", "Create draft");
    create.addEventListener("click", async () => {
      if (!name.value || !bodyTa.value) return;
      await api("/prompts", { method: "POST", body: { name: name.value, body: bodyTa.value } });
      viewPrompt(main);
    });
    draft.append(name, bodyTa, create);
    holder.appendChild(draft);

    const t = table(["ID", "Name", "Status", "Default", "A/B weight", "Actions"]);
    for (const v of data.versions) {
      const actions = el("td");
      const view = el("button", "npadmin-btn ghost", v.status === "draft" ? "View / Edit" : "View");
      view.addEventListener("click", () => openPromptEditor(main, v, false));
      actions.appendChild(view);
      if (v.status !== "published") {
        const pub = el("button", "npadmin-btn", "Publish");
        pub.addEventListener("click", async () => {
          if (!confirm("Publishing changes the cached prompt prefix and temporarily "
            + "raises cost until the cache re-warms. Continue?")) return;
          await api(`/prompts/${v.id}/publish`, { method: "POST" }); viewPrompt(main);
        });
        actions.appendChild(pub);
      }
      if (!v.is_default) {
        const arch = el("button", "npadmin-btn ghost", "Archive");
        arch.addEventListener("click", async () => {
          await api(`/prompts/${v.id}/archive`, { method: "POST" }); viewPrompt(main);
        });
        actions.appendChild(arch);
      }
      const tr = rowEls(t, [v.id, v.name, v.status, v.is_default ? "✓" : "", v.ab_weight]);
      tr.appendChild(actions);
    }
    holder.appendChild(t);

    // A/B weights editor (published only)
    const pubs = data.versions.filter((v) => v.status === "published");
    if (pubs.length >= 2) {
      const ab = el("div", "npadmin-chart");
      ab.appendChild(el("div", "npadmin-meta", "A/B weights (≥2 published with weight>0 = active split)"));
      const inputs = {};
      for (const v of pubs) {
        const lab = el("label", "npadmin-field");
        lab.appendChild(el("span", null, `${v.name} (#${v.id})`));
        const inp = el("input", "npadmin-input"); inp.type = "number"; inp.value = v.ab_weight;
        inp.style.width = "100px"; inputs[v.id] = inp; lab.appendChild(inp);
        ab.appendChild(lab);
      }
      const saveAb = el("button", "npadmin-btn", "Save weights");
      saveAb.addEventListener("click", async () => {
        const weights = Object.entries(inputs).map(([id, inp]) =>
          ({ id: Number(id), weight: Number(inp.value) || 0 }));
        await api("/prompts/ab", { method: "POST", body: { weights } });
        viewPrompt(main);
      });
      ab.appendChild(saveAb);
      holder.appendChild(ab);
    }

    await tableAB(holder);
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// Editor for a single prompt version's body.
//  - `asDraft` true (or a published/default version): saving creates a NEW draft
//    copy, since published versions are immutable. The owner then publishes it.
//  - a draft version: saving edits it in place (PUT /prompts/{id}).
function openPromptEditor(main, version, asDraft) {
  const newDraft = asDraft || version.status !== "draft";
  main.innerHTML = "";
  const back = el("button", "npadmin-btn ghost", "← Back to prompts");
  back.addEventListener("click", () => routeView(main));
  main.appendChild(back);
  main.appendChild(el("h1", "npadmin-h",
    newDraft ? `Edit "${version.name}" as new draft` : `Edit draft "${version.name}"`));

  const nameLab = el("label", "npadmin-field");
  nameLab.appendChild(el("span", null, "Version name"));
  const name = el("input", "npadmin-input");
  name.value = newDraft ? `${version.name}-edit` : version.name;
  nameLab.appendChild(name);
  main.appendChild(nameLab);

  const ta = el("textarea", "npadmin-input");
  ta.value = version.body || "";
  ta.style.minHeight = "360px"; ta.style.width = "100%";
  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn",
    newDraft ? "Save as new draft" : "Save draft");
  save.addEventListener("click", async () => {
    err.textContent = "";
    if (!ta.value.trim()) { err.textContent = "Prompt body cannot be empty"; return; }
    try {
      if (newDraft) {
        await api("/prompts", { method: "POST", body: { name: name.value, body: ta.value } });
      } else {
        await api(`/prompts/${version.id}`, { method: "PUT",
          body: { name: name.value, body: ta.value } });
      }
      routeView(main);
    } catch (e) { err.textContent = e.message; }
  });
  main.append(ta, save, err);
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
async function viewSettings(main) {
  main.appendChild(el("h1", "npadmin-h", "Settings"));
  main.appendChild(el("div", "npadmin-help",
    "Precedence: these overrides win over env defaults. Saved values apply immediately."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    await ensureMeta();
    holder.innerHTML = "";
    await systemPromptBox(holder);
    const data = await api("/settings");
    for (const key of data.keys) {
      if (key === "language") { languageSettingsBox(holder, data.resolved.language || {}); continue; }
      const box = el("div", "npadmin-chart");
      box.appendChild(el("div", "npadmin-meta", key));
      const ta = el("textarea", "npadmin-input");
      ta.value = JSON.stringify(data.resolved[key] || {}, null, 2);
      const err = el("div", "npadmin-err");
      const save = el("button", "npadmin-btn", "Save " + key);
      save.addEventListener("click", async () => {
        err.textContent = "";
        let val;
        try { val = JSON.parse(ta.value); }
        catch (_) { err.textContent = "Invalid JSON"; return; }
        if (!confirm(`Update '${key}' settings now?`)) return;
        try {
          await api(`/settings/${key}`, { method: "PUT", body: { value: val } });
          err.style.color = "var(--good)"; err.textContent = "Saved";
        } catch (e) { err.textContent = e.message; }
      });
      box.append(ta, save, err);
      holder.appendChild(box);
    }
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// Structured Layer-1 system-prompt editor. The core is split into named
// sections (tone of voice + each rule block); Layer 2 (KB) is edited in the
// Knowledge base tab, Layer 3 (player data) is supplied per request and not
// editable here. Saving composes the sections and publishes the core live as
// the new default version (one deliberate prefix-cache reset).
async function systemPromptBox(holder) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "System prompt — Layer 1 (core)"));
  box.appendChild(el("div", "npadmin-help",
    "The 3-layer prompt: Layer 1 is this core (tone of voice + the rule blocks "
    + "below); Layer 2 is the per-topic knowledge base (edited in the Knowledge "
    + "base tab); Layer 3 is the player's data, supplied per request and not "
    + "editable here. Saving applies the new core live to new sessions."));
  let data;
  try {
    data = await api("/system-prompt");
  } catch (e) { box.appendChild(errBox(e)); holder.appendChild(box); return; }

  if (data.live_version) {
    box.appendChild(el("div", "npadmin-meta",
      `Live default version: ${data.live_version.name} (#${data.live_version.id})`));
  }

  const fields = {};
  for (const m of data.meta) {
    const lab = el("label", "npadmin-field");
    lab.appendChild(el("span", null, m.label));
    const ta = el("textarea", "npadmin-input");
    ta.value = data.sections[m.key] || "";
    ta.style.minHeight = "120px"; ta.style.width = "100%";
    fields[m.key] = ta;
    lab.appendChild(ta);
    box.appendChild(lab);
  }

  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn", "Save & apply system prompt");
  save.addEventListener("click", async () => {
    err.textContent = ""; err.style.color = "";
    const sections = {};
    for (const [k, ta] of Object.entries(fields)) {
      if (!ta.value.trim()) { err.textContent = "All sections must be non-empty"; return; }
      sections[k] = ta.value;
    }
    if (!confirm("Publishing the system prompt changes the cached prompt prefix and "
      + "temporarily raises cost until the cache re-warms. Apply now?")) return;
    try {
      await api("/system-prompt", { method: "PUT", body: { sections } });
      err.style.color = "var(--good)"; err.textContent = "Saved & applied live";
    } catch (e) { err.textContent = e.message; }
  });
  box.append(save, err);
  holder.appendChild(box);
}

// Dedicated language-settings editor: a dropdown for the default answer language
// and checkboxes for the supported set — no hand-typed language codes.
function languageSettingsBox(holder, current) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "language"));

  const defLab = el("label", "npadmin-field");
  defLab.appendChild(el("span", null, "Default answer language"));
  const defSel = langSelect(current.default || state.defaultLang);
  defLab.appendChild(defSel);
  box.appendChild(defLab);

  box.appendChild(el("div", "npadmin-meta", "Supported languages"));
  const supported = new Set(current.supported || []);
  const checks = {};
  const checkRow = el("div", "npadmin-toolbar");
  for (const l of (state.languages || [])) {
    const lab = el("label", "npadmin-field");
    lab.style.flexDirection = "row"; lab.style.alignItems = "center";
    const cb = document.createElement("input"); cb.type = "checkbox";
    cb.checked = supported.has(l.code); checks[l.code] = cb;
    lab.append(cb, el("span", null, `${l.name} (${l.code})`));
    checkRow.appendChild(lab);
  }
  box.appendChild(checkRow);

  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn", "Save language");
  save.addEventListener("click", async () => {
    err.textContent = "";
    const sup = Object.entries(checks).filter(([, cb]) => cb.checked).map(([c]) => c);
    if (!sup.length) { err.textContent = "Select at least one supported language"; return; }
    if (!sup.includes(defSel.value)) { err.textContent = "Default must be a supported language"; return; }
    if (!confirm("Update 'language' settings now?")) return;
    try {
      await api("/settings/language", { method: "PUT",
        body: { value: { default: defSel.value, supported: sup } } });
      err.style.color = "var(--good)"; err.textContent = "Saved";
    } catch (e) { err.textContent = e.message; }
  });
  box.append(save, err);
  holder.appendChild(box);
}

// ---------------------------------------------------------------------------
// tiny DOM helpers
// ---------------------------------------------------------------------------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function pct(v) { return `${(v * 100).toFixed(1)}%`; }
function errBox(e) { return el("div", "npadmin-warnbox", e.message || String(e)); }
function section(main, title, node) {
  main.appendChild(el("h3", null, title)); main.appendChild(node);
}
function table(headers) {
  const t = el("table", "npadmin-table");
  const thead = el("thead"); const tr = el("tr");
  for (const h of headers) tr.appendChild(el("th", null, h));
  thead.appendChild(tr); t.appendChild(thead);
  t.appendChild(el("tbody"));
  return t;
}
function rowEls(t, cells) {
  const tr = el("tr");
  for (const c of cells) tr.appendChild(el("td", null, String(c)));
  t.querySelector("tbody").appendChild(tr);
  return tr;
}
function addRow(t, cells) { return rowEls(t, cells); }

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
if (state.token) renderApp(); else renderLogin();
