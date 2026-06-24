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
  ["kb", "Knowledge base"], ["variables", "Variables"], ["prompt", "Prompt"], ["settings", "Settings"],
  ["test", "Test sandbox"],
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
    kb: viewKB, variables: viewVariables, prompt: viewPrompt, settings: viewSettings, test: viewTest,
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
    card(cards, o.sessions_total, "Sessions (total)");
    card(cards, o.sessions_engaged, "Engaged (≥1 msg)");
    card(cards, fmtPct(o.escalation_rate), "Escalation rate");
    card(cards, fmtPct(o.resolution_rate), "Resolution rate");
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
  const t = table(["Topic", "Sessions", "Escalation rate", "Avg msgs", "Cost"]);
  for (const r of data.topics) {
    addRow(t, [r.slug, r.sessions, pct(r.escalation_rate), r.avg_messages.toFixed(1), fmtUsd(r.cost_usd_total || 0)]);
  }
  section(main, "By topic", t);
}

async function tableByLanguage(main) {
  const data = await api(`/by-language${q()}`);
  const t = table(["Language", "Sessions", "Escalation rate", "Cost"]);
  for (const r of data.languages) addRow(t, [r.lang, r.sessions, pct(r.escalation_rate), fmtUsd(r.cost_usd_total || 0)]);
  section(main, "By language", t);
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
      const t = table(["Created", "Topic", "Lang", "Status", "Msgs", "Cost", ""]);
      for (const s of data.items) {
        const tr = addRow(t, [s.created_at.slice(0, 16).replace("T", " "),
          s.topic || "—", s.lang || "—",
          s.escalated ? "escalated" : s.status, s.message_count, fmtUsd(s.cost_usd_total || 0), "view →"]);
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
      + ` · lang=${d.session.lang || "—"} · cost=$${d.cost_usd_total}`;
    main.appendChild(meta);

    const row = el("div", "npadmin-row");
    const convo = el("div", "npadmin-col");
    // Interleave messages and topic-switch markers by time so the whole path is
    // traceable: a cross-topic turn suppresses its answer and persists no message,
    // so its detect-call cost would otherwise be invisible and the per-turn costs
    // would not add up to the session total. The marker shows from→to + that cost.
    const items = [];
    for (const m of d.messages) items.push({ at: m.created_at, kind: "msg", m });
    for (const e of (d.events || [])) items.push({ at: e.created_at, kind: "switch", e });
    items.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
    for (const it of items) {
      if (it.kind === "switch") {
        const p = it.e.payload || {};
        const marker = el("div", "npadmin-switch",
          `↪ Topic switch: ${p.from || "—"} → ${p.to || "—"}`);
        marker.appendChild(el("div", "npadmin-meta",
          `routing call · $${p.cost_usd ?? 0}`));
        convo.appendChild(marker);
        continue;
      }
      const m = it.m;
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
    "Open or escalated sessions grouped by topic — read clusters and grow the KB."));
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
      const t = table(["First message", "Status", "Msgs", "Session"]);
      for (const s of g.sessions) {
        const tr = addRow(t, [s.first_message || "—", s.escalated ? "escalated" : s.status, s.message_count, s.session_id.slice(0, 8)]);
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
    "One knowledge-base text per topic, injected into the prompt for that topic. "
    + "Edit a topic's text below and save."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api("/kb/topics");
    holder.innerHTML = "";

    for (const topic of data.topics) {
      const tt = topic.title.en || topic.title.ru || topic.slug;
      holder.appendChild(el("h3", null, `${tt} — ${topic.slug}`));

      const ta = el("textarea", "npadmin-input");
      ta.placeholder = "Knowledge-base text for this topic…";
      ta.style.minHeight = "180px"; ta.style.width = "100%";
      const current = await api(`/kb/content?topic_id=${topic.id}`);
      ta.value = current.content || "";

      const err = el("div", "npadmin-err");
      const save = el("button", "npadmin-btn", "Save");
      save.addEventListener("click", async () => {
        err.textContent = "";
        try {
          await api("/kb/content", { method: "PUT",
            body: { topic_id: topic.id, content: ta.value } });
          save.textContent = "Saved ✓";
          setTimeout(() => { save.textContent = "Save"; }, 1500);
        } catch (e) { err.textContent = e.message; }
      });

      const clear = el("button", "npadmin-btn ghost", "Clear");
      clear.addEventListener("click", async () => {
        if (!confirm("Clear this topic's knowledge base?")) return;
        try {
          await api(`/kb/content?topic_id=${topic.id}`, { method: "DELETE" });
          ta.value = "";
        } catch (e) { err.textContent = e.message; }
      });

      holder.append(ta, save, clear, err);
    }
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}


// ---------------------------------------------------------------------------
// Knowledge-base variables
// ---------------------------------------------------------------------------
async function viewVariables(main) {
  main.appendChild(el("h1", "npadmin-h", "Variables"));
  main.appendChild(el("div", "npadmin-help",
    "Admin-managed values for placeholders used inside knowledge-base texts. "
    + "When a KB answer contains a token like {min_deposit}, the prompt receives "
    + "the value from this registry. Current defaults come from the TEST column."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api("/kb/variables");
    holder.innerHTML = "";
    const filter = el("input", "npadmin-input");
    filter.placeholder = "Filter by variable, description, or value…";
    filter.style.marginBottom = "12px";
    holder.appendChild(filter);

    const tableWrap = el("div", "npadmin-chart");
    holder.appendChild(tableWrap);

    function renderRows() {
      tableWrap.innerHTML = "";
      const term = filter.value.trim().toLowerCase();
      const rows = (data.variables || []).filter((v) => {
        const hay = `${v.key} ${v.description} ${v.value}`.toLowerCase();
        return !term || hay.includes(term);
      });
      const t = table(["Variable", "Description", "Value", ""]);
      for (const v of rows) {
        const tr = el("tr");
        tr.appendChild(el("td", "npadmin-codecell", `{${v.key}}`));
        tr.appendChild(el("td", null, v.description || "—"));
        const valueTd = el("td");
        const valueInput = el("textarea", "npadmin-input");
        valueInput.value = v.value || "";
        valueInput.style.minHeight = "68px";
        valueInput.style.fontFamily = "inherit";
        valueTd.appendChild(valueInput);
        tr.appendChild(valueTd);
        const actionTd = el("td");
        const status = el("div", "npadmin-err");
        const save = el("button", "npadmin-btn", "Save");
        save.addEventListener("click", async () => {
          status.textContent = ""; status.style.color = "";
          try {
            const res = await api(`/kb/variables/${encodeURIComponent(v.key)}`, {
              method: "PUT",
              body: { key: v.key, description: v.description || "", value: valueInput.value },
            });
            v.value = res.variable.value;
            status.style.color = "var(--good)";
            status.textContent = "Saved";
          } catch (e) { status.textContent = e.message; }
        });
        actionTd.append(save, status);
        tr.appendChild(actionTd);
        t.querySelector("tbody").appendChild(tr);
      }
      tableWrap.appendChild(t);
      tableWrap.appendChild(el("div", "npadmin-meta", `${rows.length} variables`));
    }

    filter.addEventListener("input", renderRows);
    renderRows();
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Prompt (read-only)
//
// The prompt is sourced solely from the server file `prompts.py` — the single
// source of truth — and is NOT editable from the admin. This tab just renders the
// complete prompt the model receives (all layers) so the owner can see exactly
// how it's assembled. To change the prompt, edit prompts.py and redeploy.
// ---------------------------------------------------------------------------
async function viewPrompt(main) {
  main.appendChild(el("h1", "npadmin-h", "Prompt"));
  main.appendChild(el("div", "npadmin-help",
    "Read-only. The prompt lives in the server file prompts.py — the single "
    + "source of truth — and is not editable here. Below is the COMPLETE prompt "
    + "the model receives, assembled exactly as it's sent. To change it, edit "
    + "prompts.py and redeploy. (Knowledge-base answers — Layer 2 — are still "
    + "editable in the Knowledge base tab.)"));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api("/effective-prompt");
    holder.innerHTML = "";
    effectivePreviewBox(holder, data.effective_preview);
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
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
    const data = await api("/settings");
    for (const key of data.keys) {
      if (key === "language") { languageSettingsBox(holder, data.resolved.language || {}); continue; }
      if (key === "model") { modelSettingsBox(holder, data.resolved.model || {}); continue; }
      if (key === "general") { generalSettingsBox(holder, data.resolved.general || {}); continue; }
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

// Read-only "full effective prompt" preview. Renders the WHOLE prompt exactly as
// it's sent to the model (all 3 layers) for an example player + topic, so the
// owner can see and verify everything in one place. The prompt is sourced from
// the server file prompts.py (the single source of truth) and is not editable.
function effectivePreviewBox(holder, pv) {
  if (!pv) return;
  const box = el("div", "npadmin-chart");
  const ex = pv.example || {};
  box.appendChild(el("div", "npadmin-meta", "Full effective prompt (all layers, as sent)"));
  box.appendChild(el("div", "npadmin-help",
    "Read-only. The COMPLETE prompt the model receives — Layer 1 (core) + Layer 2 "
    + "(the selected topic's knowledge base) in the system message, then the "
    + "Layer-3 directives + the player's data in the user message. Sourced from "
    + `prompts.py. Example: topic «${ex.topic || "—"}», language `
    + `${ex.lang || "—"}, sample player. Layer 2 (KB) and player data vary per request.`));

  const sysWrap = el("label", "npadmin-field");
  sysWrap.appendChild(el("span", null, "System message — Layer 1 (core) + Layer 2 (KB block)"));
  const sysPre = el("pre", "npadmin-code"); sysPre.textContent = pv.system;
  sysWrap.appendChild(sysPre); box.appendChild(sysWrap);

  const usrWrap = el("label", "npadmin-field");
  usrWrap.appendChild(el("span", null, "User message — Layer 3 (directives + player context + message)"));
  const usrPre = el("pre", "npadmin-code"); usrPre.textContent = pv.user;
  usrWrap.appendChild(usrPre); box.appendChild(usrWrap);

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

// Dedicated OpenAI model-tuning editor: the knobs that used to live in Railway
// env (model name, sampling, timeouts, retries, per-key concurrency). Saved
// values win over env and apply live — the client is rebuilt server-side so the
// request timeout and concurrency take effect immediately too.
function modelSettingsBox(holder, current) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "model — OpenAI tuning"));
  box.appendChild(el("div", "npadmin-help",
    "Model name + reasoning/timeout/retry knobs. These override the Railway env "
    + "vars (OPENAI_MODEL, OPENAI_REASONING_EFFORT, …). The default is the GPT-5 mini "
    + "reasoning family: it has no temperature; control it with reasoning effort + "
    + "verbosity instead. Changes apply to new requests immediately; no redeploy. "
    + "Secrets (API keys) stay in Railway."));

  // [field, label, type, step/extra]
  const NUM = [
    ["max_output_tokens", "Max output tokens", "number", "1"],
    ["request_timeout_sec", "Request timeout (sec)", "number", "1"],
    ["key_switch_timeout_sec", "Key switch timeout (sec)", "number", "1"],
    ["max_attempts", "Max attempts (retries)", "number", "1"],
    ["max_concurrent_per_key", "Max concurrent per key", "number", "1"],
  ];
  const fields = {};

  const nameLab = el("label", "npadmin-field");
  nameLab.appendChild(el("span", null, "Model name"));
  const nameInp = el("input", "npadmin-input");
  nameInp.type = "text"; nameInp.value = current.model || "";
  nameInp.placeholder = "gpt-5-mini";
  nameLab.appendChild(nameInp);
  box.appendChild(nameLab);

  // GPT-5 reasoning knobs. "" ⇒ omit the parameter (use the model's default).
  const selects = {};
  const buildLevel = (key, label, value) => {
    const lab = el("label", "npadmin-field");
    lab.appendChild(el("span", null, label));
    const sel = el("select", "npadmin-input"); sel.style.width = "auto";
    for (const opt of ["", "low", "medium", "high"]) {
      const o = el("option", null, opt === "" ? "(model default)" : opt);
      o.value = opt;
      if ((value || "") === opt) o.selected = true;
      sel.appendChild(o);
    }
    selects[key] = sel;
    lab.appendChild(sel);
    box.appendChild(lab);
  };
  buildLevel("reasoning_effort", "Reasoning effort", current.reasoning_effort);
  buildLevel("verbosity", "Verbosity", current.verbosity);

  for (const [key, label, type, step] of NUM) {
    const lab = el("label", "npadmin-field");
    lab.appendChild(el("span", null, label));
    const inp = el("input", "npadmin-input");
    inp.type = type; inp.step = step;
    inp.value = current[key] != null ? current[key] : "";
    fields[key] = inp;
    lab.appendChild(inp);
    box.appendChild(lab);
  }

  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn", "Save model settings");
  save.addEventListener("click", async () => {
    err.textContent = ""; err.style.color = "";
    const name = nameInp.value.trim();
    if (!name) { err.textContent = "Model name is required"; return; }
    const value = { model: name };
    value.reasoning_effort = selects.reasoning_effort.value;
    value.verbosity = selects.verbosity.value;
    for (const [key] of NUM) {
      value[key] = parseInt(fields[key].value, 10);
    }
    for (const [key, label] of NUM) {
      if (Number.isNaN(value[key])) { err.textContent = `${label}: enter a number`; return; }
    }
    if (!confirm("Update 'model' settings now? Applies to new requests immediately.")) return;
    try {
      await api("/settings/model", { method: "PUT", body: { value } });
      err.style.color = "var(--good)"; err.textContent = "Saved — live";
    } catch (e) { err.textContent = e.message; }
  });
  box.append(save, err);
  holder.appendChild(box);
}

// Dedicated "general" operational editor: session lifetime, the escalation
// contact-button URL, and the request body cap. These used to live in Railway
// env (SESSION_TTL_HOURS, CONTACT_FORM_URL, BODY_MAX_BYTES); now they're tuned
// here and apply without a redeploy.
function generalSettingsBox(holder, current) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "general — operational"));
  box.appendChild(el("div", "npadmin-help",
    "Session lifetime, the escalation contact button URL, and the max request "
    + "body size. Overrides the matching Railway env vars; applies live."));

  const urlLab = el("label", "npadmin-field");
  urlLab.appendChild(el("span", null, "Contact form URL (escalation button)"));
  const urlInp = el("input", "npadmin-input");
  urlInp.type = "text"; urlInp.value = current.contact_form_url || "";
  urlInp.placeholder = "https://nikabet.example/support";
  urlLab.appendChild(urlInp);
  box.appendChild(urlLab);

  const NUM = [
    ["session_ttl_hours", "Session TTL (hours)", "1"],
    ["body_max_bytes", "Max request body (bytes)", "1024"],
  ];
  const fields = {};
  for (const [key, label, step] of NUM) {
    const lab = el("label", "npadmin-field");
    lab.appendChild(el("span", null, label));
    const inp = el("input", "npadmin-input");
    inp.type = "number"; inp.step = step;
    inp.value = current[key] != null ? current[key] : "";
    fields[key] = inp;
    lab.appendChild(inp);
    box.appendChild(lab);
  }

  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn", "Save general settings");
  save.addEventListener("click", async () => {
    err.textContent = ""; err.style.color = "";
    const value = { contact_form_url: urlInp.value.trim() };
    for (const [key, label] of NUM) {
      value[key] = parseInt(fields[key].value, 10);
      if (Number.isNaN(value[key])) { err.textContent = `${label}: enter a number`; return; }
    }
    if (!confirm("Update 'general' settings now?")) return;
    try {
      await api("/settings/general", { method: "PUT", body: { value } });
      err.style.color = "var(--good)"; err.textContent = "Saved — live";
    } catch (e) { err.textContent = e.message; }
  });
  box.append(save, err);
  holder.appendChild(box);
}

// ---------------------------------------------------------------------------
// Test sandbox — the stand-in player profile for the test widget (test page /)
//
// In production the host site supplies user_context over a signed handshake;
// in test/dev this stored profile stands in for it. It feeds Layer 3 of the
// prompt (so the model can greet the player by name) and can pin the answer/UI
// language for the whole session — handy when the browser locale doesn't match
// the language you want to test.
// ---------------------------------------------------------------------------
async function viewTest(main) {
  main.appendChild(el("h1", "npadmin-h", "Test sandbox"));
  main.appendChild(el("div", "npadmin-help",
    "The player profile used by the test widget (test page at /). In production "
    + "the host site supplies this over a signed handshake; here it stands in for "
    + "it. These fields feed Layer 3 of the prompt (the model can address the "
    + "player by name). The session language always follows the browser."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api("/test-profile");
    holder.innerHTML = "";
    const p = data.profile || {};

    if (!data.active) {
      holder.appendChild(el("div", "npadmin-warnbox",
        "A handshake secret (WIDGET_HANDSHAKE_SECRET) is configured, so the host "
        + "site is authoritative and this test profile is ignored at session create."));
    }

    const box = el("div", "npadmin-chart");

    // enabled toggle
    const enLab = el("label", "npadmin-field");
    enLab.style.flexDirection = "row"; enLab.style.alignItems = "center";
    const en = document.createElement("input");
    en.type = "checkbox"; en.checked = p.enabled !== false;
    enLab.append(en, el("span", null,
      " Enabled (off ⇒ fall back to the widget's built-in context)"));
    box.appendChild(enLab);

    // plain text fields (the four the model actually sees in Layer 3)
    const fields = {};
    const textFields = [
      ["id", "Player id"], ["full_name", "Full name (used to greet by name)"],
      ["email", "Email"], ["activation_status", "Activation status"],
      ["country", "Country"], ["balance", "Balance"],
      ["vip_level", "VIP level"], ["registration_date", "Registration date"],
    ];
    for (const [key, label] of textFields) {
      const lab = el("label", "npadmin-field");
      lab.appendChild(el("span", null, label));
      const inp = el("input", "npadmin-input");
      inp.value = p[key] || "";
      fields[key] = inp; lab.appendChild(inp);
      box.appendChild(lab);
    }

    const err = el("div", "npadmin-err");
    const save = el("button", "npadmin-btn", "Save test profile");
    save.addEventListener("click", async () => {
      err.textContent = ""; err.style.color = "";
      const value = {
        enabled: en.checked,
        id: fields.id.value, full_name: fields.full_name.value,
        email: fields.email.value, activation_status: fields.activation_status.value,
        country: fields.country.value, balance: fields.balance.value,
        vip_level: fields.vip_level.value, registration_date: fields.registration_date.value,
      };
      try {
        await api("/test-profile", { method: "PUT", body: { value } });
        err.style.color = "var(--good)";
        err.textContent = "Saved — applies to the next chat session (reopen the widget)";
      } catch (e) { err.textContent = e.message; }
    });

    const open = el("a", "npadmin-btn ghost", "Open test page ↗");
    open.href = "/"; open.target = "_blank"; open.style.marginLeft = "8px";

    const actions = el("div", "npadmin-toolbar");
    actions.append(save, open);
    box.append(actions, err);
    holder.appendChild(box);
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
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
function fmtUsd(v) { return `$${Number(v).toFixed(4)}`; }
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
