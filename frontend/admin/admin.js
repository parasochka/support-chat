// NowPlix support — admin dashboard SPA. Vanilla ES module, no build step.
// All DOM classes are prefixed `npadmin-` to avoid host-page collisions.

const TOKEN_KEY = "npadmin_token";

// ---------------------------------------------------------------------------
// URL hash routing helpers
// ---------------------------------------------------------------------------
// Hash format: #view  or  #view/PARAM  (a session id under #sessions/…, a
// sub-tab id under #kb/… and #prompt/…).
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  if (!raw) return normalizeRoute("overview", null);
  const slash = raw.indexOf("/");
  return slash === -1
    ? normalizeRoute(raw, null)
    : normalizeRoute(raw.slice(0, slash), raw.slice(slash + 1));
}
// Map removed top-level tabs to their new homes so old bookmarks keep working:
// #variables -> the KB Variables sub-tab, #test -> the Prompt Variables sub-tab
// (the test player moved there as a block).
function normalizeRoute(view, param) {
  if (view === "variables") return { view: "kb", param: "variables" };
  if (view === "test") return { view: "prompt", param: "variables" };
  return { view, param };
}
function pushHash(view, param) {
  const h = param ? `#${view}/${param}` : `#${view}`;
  if (location.hash !== h) history.pushState(null, "", h);
}

function isoToday() { return new Date().toISOString().slice(0, 10); }
function isoDaysAgo(n) {
  const d = new Date(); d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

// Sidebar tabs (id + label, optional adminOnly flag). `_VALID_VIEWS` is derived
// from this so the two can never drift apart when a tab is added/removed.
// adminOnly views (the technical/management tabs) are hidden from managers, who
// get a read-only support view; the server enforces this regardless of the UI.
const VIEWS = [
  ["overview", "Overview"], ["sessions", "Sessions"], ["unresolved", "Unresolved"],
  ["kb", "Knowledge base"], ["prompt", "Prompt"], ["translations", "Translations"],
  ["retention", "Retention · Telegram", true],
  ["structure", "Structure", true], ["settings", "Settings", true],
  ["users", "Users", true],
];
const _VALID_VIEWS = VIEWS.map(([id]) => id);
const WRITE_ROLES = ["admin"];
const SCOPE_KEY = "npadmin_scope";

// Decode role/email out of the admin JWT payload (no verification — the server
// is authoritative; this only drives which tabs/controls the SPA shows).
function decodeToken(token) {
  try {
    let b = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    b += "=".repeat((4 - (b.length % 4)) % 4);
    const p = JSON.parse(atob(b));
    return { role: p.role || null, email: p.email || null };
  } catch (_) { return { role: null, email: null }; }
}

const _boot = state_token_decode();
function state_token_decode() {
  const token = sessionStorage.getItem(TOKEN_KEY) || null;
  return { token, ...(token ? decodeToken(token) : { role: null, email: null }) };
}

const state = {
  token: _boot.token,
  role: _boot.role,
  email: _boot.email,
  view: (() => { const { view } = parseHash(); return _VALID_VIEWS.includes(view) ? view : "overview"; })(),
  // Sub-route under the view: a sub-tab id for #kb/… and #prompt/… (session ids
  // under #sessions/… are handled separately by openSession).
  param: (() => { const { view, param } = parseHash(); return _VALID_VIEWS.includes(view) ? param : null; })(),
  from: isoDaysAgo(30),
  to: isoToday(),
  // Supported languages (loaded once from /admin/meta) for the dropdowns.
  languages: null,
  supported: null,
  isoCatalog: null,
  defaultLang: "ru",
  // Multi-tenancy: the partner→product tree the account may see (loaded from
  // /admin/structure) + the caller's scope info (/admin/me), and the SELECTED
  // scope for the header switcher. productId=null + partnerId=null = "all I
  // can see"; content tabs then act on the default product (global accounts).
  structure: null,          // [{id, slug, name, products: [...], role}, ...]
  globalRole: null,         // 'admin' | 'manager' | null
  memberships: [],
  scope: (() => {           // restored synchronously so a reload keeps the scope
    try { return JSON.parse(localStorage.getItem(SCOPE_KEY)) || { partnerId: null, productId: null }; }
    catch (_) { return { partnerId: null, productId: null }; }
  })(),
};

// --- tenancy scope helpers ---------------------------------------------------
function saveScope() {
  try { localStorage.setItem(SCOPE_KEY, JSON.stringify(state.scope)); } catch (_) {}
}
// Query-string fragment for DASHBOARD endpoints (product beats partner).
function scopeQS() {
  if (state.scope.productId) return `&product_id=${state.scope.productId}`;
  if (state.scope.partnerId) return `&partner_id=${state.scope.partnerId}`;
  return "";
}
// Query string for PRODUCT-scoped content endpoints. Empty when no product is
// selected — the server then falls back to the default product.
function productQS(first) {
  if (!state.scope.productId) return "";
  return `${first ? "?" : "&"}product_id=${state.scope.productId}`;
}
function allProducts() {
  const out = [];
  for (const pa of (state.structure || [])) for (const pr of (pa.products || [])) out.push(pr);
  return out;
}
function currentProduct() {
  return allProducts().find((p) => p.id === state.scope.productId) || null;
}
// The product a content tab is editing right now: the selected one, else the
// boot-seeded default (which is what the server falls back to).
function editedProduct() {
  return currentProduct()
    || allProducts().find((p) => p.slug === "default")
    || null;
}
// A small hint line for content tabs naming the product being edited.
function scopeHint() {
  const p = editedProduct();
  const label = p ? `${p.name} (${p.slug})` : "default product";
  return el("div", "npadmin-scopehint",
    `Editing product: ${label} — switch partner/product in the header.`);
}

// Write permission for the current role. Managers are read-only; admins write.
function canWrite() { return WRITE_ROLES.includes(state.role); }
// The tabs this role may see (managers lose the adminOnly technical tabs).
function allowedViews() {
  return VIEWS.filter(([, , adminOnly]) => !adminOnly || canWrite());
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

function q() { return `?from=${state.from}&to=${state.to}${scopeQS()}`; }

// Load the caller's scope info + the visible partner→product tree (once per
// session; re-fetched after structure edits via refreshStructure()).
async function ensureStructure(force = false) {
  if (state.structure && !force) return;
  try {
    const [me, st] = await Promise.all([api("/me"), api("/structure")]);
    state.globalRole = me.global_role || null;
    state.memberships = me.memberships || [];
    state.structure = st.partners || [];
  } catch (_) {
    state.structure = state.structure || [];
  }
  // Validate the restored scope against what this account can actually see;
  // a non-global account with nothing selected lands on its first product.
  const products = allProducts();
  if (state.scope.productId && !products.some((p) => p.id === state.scope.productId)) {
    state.scope.productId = null;
  }
  if (state.scope.partnerId
      && !(state.structure || []).some((pa) => pa.id === state.scope.partnerId)) {
    state.scope.partnerId = null;
  }
  if (!state.globalRole && !state.scope.productId && !state.scope.partnerId) {
    if (products.length) {
      state.scope.productId = products[0].id;
      state.scope.partnerId = products[0].partner_id;
    }
  }
  saveScope();
}

// Load the supported-language list once (for the language dropdowns). Falls
// back to a sane default set if the meta call fails so the UI still works.
async function ensureMeta() {
  if (state.languages) return;
  try {
    const m = await api(`/meta${productQS(true)}`);
    state.languages = m.languages || [];
    state.supported = m.supported || (m.languages || []).map((l) => l.code);
    state.isoCatalog = m.iso_catalog || [];
    if (m.default_language) state.defaultLang = m.default_language;
  } catch (_) {
    state.languages = [
      { code: "en", name: "English" }, { code: "es", name: "Spanish" },
      { code: "ru", name: "Russian" }, { code: "tr", name: "Turkish" },
      { code: "pt", name: "Portuguese" },
    ];
    state.supported = state.languages.map((l) => l.code);
    state.isoCatalog = [];
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
  state.token = null; state.role = null; state.email = null;
  sessionStorage.removeItem(TOKEN_KEY);
  renderLogin();
}

function renderLogin() {
  const root = document.getElementById("npadmin-root");
  root.innerHTML = "";
  const box = el("div", "npadmin-login");
  box.appendChild(el("h1", null, "NowPlix Support — Admin"));
  // Email is required: every admin signs in as a named admin/manager account.
  const emailInp = el("input", "npadmin-input");
  emailInp.type = "email"; emailInp.placeholder = "Email"; emailInp.required = true;
  emailInp.autocomplete = "username";
  const inp = el("input", "npadmin-input");
  inp.type = "password"; inp.placeholder = "Password";
  inp.style.marginTop = "8px"; inp.autocomplete = "current-password";
  const btn = el("button", "npadmin-btn", "Sign in");
  btn.style.marginTop = "12px";
  const err = el("div", "npadmin-err");
  async function doLogin() {
    err.textContent = "";
    const email = emailInp.value.trim();
    if (!email) { err.textContent = "Email is required"; emailInp.focus(); return; }
    if (!inp.value) { err.textContent = "Password is required"; inp.focus(); return; }
    try {
      const res = await fetch("/admin/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password: inp.value }),
      });
      const data = await res.json();
      if (!res.ok) { err.textContent = data.detail || "Login failed"; return; }
      state.token = data.token;
      state.role = data.role || decodeToken(data.token).role;
      state.email = data.email || decodeToken(data.token).email;
      sessionStorage.setItem(TOKEN_KEY, data.token);
      renderApp();
    } catch (e) { err.textContent = "Network error"; }
  }
  btn.addEventListener("click", doLogin);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  emailInp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.focus(); });
  box.append(emailInp, inp, btn, err);
  root.appendChild(box);
  emailInp.focus();
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------
// Reflect the active tab in the sidebar without a full re-render.
function syncNavActive() {
  document.querySelectorAll(".npadmin-nav button").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.view === state.view));
}

function renderApp() {
  const root = document.getElementById("npadmin-root");
  root.innerHTML = "";
  const app = el("div", "npadmin-app");

  // Drawer open/close (mobile only — on desktop the sidebar is always visible
  // and these classes are inert). Toggling a class on `app` drives the CSS.
  const closeDrawer = () => app.classList.remove("drawer-open");
  const toggleDrawer = () => app.classList.toggle("drawer-open");

  // Top bar — visible only on narrow viewports (CSS-gated). Holds the
  // hamburger that reveals the off-canvas sidebar + the brand.
  const topbar = el("div", "npadmin-topbar");
  const burger = el("button", "npadmin-burger");
  burger.setAttribute("aria-label", "Menu");
  burger.innerHTML =
    '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">' +
    '<path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" fill="none"/></svg>';
  burger.addEventListener("click", toggleDrawer);
  topbar.append(burger, el("div", "npadmin-brand", "NowPlix Admin"));

  // Tap-away scrim that closes the drawer.
  const scrim = el("div", "npadmin-scrim");
  scrim.addEventListener("click", closeDrawer);

  // A manager landing (via URL hash) on a hidden technical tab falls back to overview.
  if (!allowedViews().some(([id]) => id === state.view)) state.view = "overview";

  const side = el("div", "npadmin-side");
  side.appendChild(el("div", "npadmin-brand", "NowPlix Admin"));
  const nav = el("div", "npadmin-nav");
  for (const [id, label] of allowedViews()) {
    const b = el("button", id === state.view ? "active" : null, label);
    b.dataset.view = id;
    b.addEventListener("click", () => {
      state.view = id;
      state.param = null;
      pushHash(id);
      syncNavActive();
      closeDrawer();
      routeView(main);
    });
    nav.appendChild(b);
  }
  side.appendChild(nav);
  const out = el("button", "npadmin-btn ghost npadmin-logout", "Sign out");
  out.addEventListener("click", logout);
  side.appendChild(out);

  const main = el("div", "npadmin-main");
  main.id = "npadmin-main";

  // The tenancy header: a persistent Partner → Product switcher block sitting
  // above the content ("в шапке"). Every data call carries the selection, so
  // switching re-scopes the whole panel — dashboard, KB, prompt, translations,
  // settings — to that casino.
  const maincol = el("div", "npadmin-maincol");
  const scopebar = el("div", "npadmin-scopebar");
  scopebar.id = "npadmin-scopebar";
  maincol.append(scopebar, main);
  app.append(topbar, scrim, side, maincol);
  root.appendChild(app);

  ensureStructure().then(() => {
    renderScopeBar(scopebar, main);
    // The initial route may have raced ahead of the structure load; a
    // non-global account may have just been snapped onto its first product —
    // re-route so the visible data matches the resolved scope.
    const { view: v2, param: p2 } = parseHash();
    if (!(v2 === "sessions" && p2)) routeView(main);
  });

  // If the URL points at a specific session, open it directly after the shell is ready.
  const { view: hv, param: hParam } = parseHash();
  if (hv === "sessions" && hParam) {
    openSession(hParam);
  } else {
    routeView(main);
  }
}

// The Partner → Product selects in the header block. "All" options collapse the
// scope upward: product=All shows the partner aggregate, partner=All shows
// everything the account may see (global accounts).
function renderScopeBar(bar, main) {
  bar.innerHTML = "";
  const partners = state.structure || [];
  const label = el("span", "npadmin-scopelabel", "Scope");
  const paSel = el("select", "npadmin-input"); paSel.style.width = "auto";
  const prSel = el("select", "npadmin-input"); prSel.style.width = "auto";

  const paAll = el("option", null, state.globalRole ? "All partners" : "My partners");
  paAll.value = ""; paSel.appendChild(paAll);
  for (const pa of partners) {
    const o = el("option", null, pa.name + (pa.active === false ? " (off)" : ""));
    o.value = String(pa.id);
    if (pa.id === state.scope.partnerId) o.selected = true;
    paSel.appendChild(o);
  }

  function fillProducts() {
    prSel.innerHTML = "";
    const prAll = el("option", null, "All products");
    prAll.value = ""; prSel.appendChild(prAll);
    const pool = state.scope.partnerId
      ? (partners.find((pa) => pa.id === state.scope.partnerId) || {}).products || []
      : allProducts();
    for (const pr of pool) {
      const o = el("option", null, pr.name + (pr.active === false ? " (off)" : ""));
      o.value = String(pr.id);
      if (pr.id === state.scope.productId) o.selected = true;
      prSel.appendChild(o);
    }
  }
  fillProducts();

  paSel.addEventListener("change", () => {
    state.scope.partnerId = paSel.value ? parseInt(paSel.value, 10) : null;
    state.scope.productId = null;
    state.languages = null;   // language set can differ per product — refetch
    fillProducts(); saveScope(); routeView(main);
  });
  prSel.addEventListener("change", () => {
    state.scope.productId = prSel.value ? parseInt(prSel.value, 10) : null;
    const p = currentProduct();
    if (p) { state.scope.partnerId = p.partner_id; paSel.value = String(p.partner_id); }
    state.languages = null;
    saveScope(); routeView(main);
  });

  bar.append(label, el("span", "npadmin-scopelabel", "Partner"), paSel,
             el("span", "npadmin-scopelabel", "Product"), prSel);
  const who = el("span", "npadmin-scopewho",
    state.email ? `${state.email}${state.globalRole ? " · global " + state.globalRole : ""}` : "");
  bar.appendChild(who);
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

// Bumped on every routeView so an in-flight async view can tell it has been
// superseded. Without this, two overlapping renders of the same view (e.g. the
// initial route + the post-structure re-route in renderApp) both kept appending
// into `main` after their awaits — the overview's charts and tables showed up
// twice.
let routeGen = 0;

function routeView(main) {
  routeGen += 1;
  main.innerHTML = "";
  // Guard: managers cannot reach the technical/management views even by URL.
  if (!allowedViews().some(([id]) => id === state.view)) {
    state.view = "overview"; state.param = null;
    pushHash("overview"); syncNavActive();
  }
  const map = {
    overview: viewOverview, sessions: viewSessions, unresolved: viewUnresolved,
    kb: viewKB, prompt: viewPrompt, translations: viewTranslations,
    retention: viewRetention,
    structure: viewStructure, settings: viewSettings, users: viewUsers,
  };
  (map[state.view] || viewOverview)(main, state.param);
}

// A row of sub-tab buttons under a view's heading (e.g. Knowledge base:
// Content | Variables). Clicking one updates the hash (#view/sub) and re-routes.
function subTabs(main, view, tabs, active) {
  const bar = el("div", "npadmin-toolbar npadmin-subtabs");
  for (const [id, label] of tabs) {
    const isActive = id === active;
    const b = el("button", "npadmin-btn" + (isActive ? "" : " ghost"), label);
    if (!isActive) {
      b.addEventListener("click", () => {
        state.param = id;
        pushHash(view, id);
        routeView(main);
      });
    }
    bar.appendChild(b);
  }
  main.appendChild(bar);
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
async function viewOverview(main) {
  const gen = routeGen; // superseded when routeView runs again
  main.appendChild(el("h1", "npadmin-h", "Overview"));
  main.appendChild(dateToolbar(() => routeView(main)));
  // All containers are created and attached BEFORE the first await: a
  // superseded render's containers get detached by the next routeView's
  // main.innerHTML = "", so its late appends can never show up on screen.
  const cards = el("div", "npadmin-cards"); main.appendChild(cards);
  const charts = el("div", "npadmin-chartgrid"); main.appendChild(charts);
  const tables = el("div", "npadmin-2col"); main.appendChild(tables);
  cards.appendChild(el("div", "npadmin-meta", "Loading…"));
  try {
    const o = await api(`/overview${q()}`);
    if (gen !== routeGen) return; // a newer route owns the screen now
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

    await chartFor(charts, "sessions", "Sessions over time",
      { format: (v) => String(Math.round(v)), color: "#4f8cff" });
    await chartFor(charts, "cost", "Cost over time", { format: fmtUsd, color: "#36c08a" });
    await chartFor(charts, "cost_per_session", "Avg cost / session per day",
      { format: fmtUsd, color: "#b483e8" });
    await chartFor(charts, "escalation_rate", "Escalation rate over time",
      { format: pct, color: "#e8b349" });

    if (gen !== routeGen) return;
    await tableByTopic(tables);
    await tableByLanguage(tables);
  } catch (e) { cards.innerHTML = ""; cards.appendChild(errBox(e)); }
}

function card(parent, value, label, note) {
  const c = el("div", "npadmin-card");
  c.appendChild(el("div", "v", String(value)));
  c.appendChild(el("div", "l", label));
  if (note) c.appendChild(el("div", "npadmin-proxy", note));
  parent.appendChild(c);
}

async function chartFor(main, metric, title, opts = {}) {
  const fmt = opts.format || ((v) => String(v));
  const wrap = el("div", "npadmin-chart");
  const head = el("div", "npadmin-charthead");
  head.appendChild(el("div", "npadmin-charttitle", title));
  const summary = el("div", "npadmin-chartsum");
  head.appendChild(summary);
  wrap.appendChild(head);
  main.appendChild(wrap);
  try {
    const data = await api(`/timeseries${q()}&metric=${metric}&bucket=day`);
    const series = data.series || [];
    // A compact at-a-glance summary so each panel reads professionally even
    // before the user hovers: latest point + the period's average.
    if (series.length) {
      const vals = series.map((d) => d.value);
      const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
      const last = vals[vals.length - 1];
      summary.appendChild(el("span", "npadmin-chipv", `latest ${fmt(last)}`));
      summary.appendChild(el("span", "npadmin-chipm", `avg ${fmt(avg)}`));
    }
    wrap.appendChild(lineChart(series, { ...opts, format: fmt }));
  } catch (e) { wrap.appendChild(errBox(e)); }
}

// Hand-rolled interactive inline SVG line chart (no external dependency).
// Renders a gradient area, dated x-axis ticks, y-axis gridlines, and a hover
// tooltip that reads out the exact value/date under the cursor.
function lineChart(series, opts = {}) {
  const fmt = opts.format || ((v) => String(v));
  const color = opts.color || "#4f8cff";
  const W = 720, H = 200;
  const m = { t: 12, r: 14, b: 26, l: 46 };
  const NS = "http://www.w3.org/2000/svg";
  const box = el("div", "npadmin-chartbox");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  box.appendChild(svg);

  if (!series || !series.length) {
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", 14); t.setAttribute("y", 26); t.setAttribute("fill", "#9aa7c2");
    t.setAttribute("font-size", "12"); t.textContent = "No data in range";
    svg.appendChild(t); return box;
  }

  const plotW = W - m.l - m.r, plotH = H - m.t - m.b;
  const vals = series.map((d) => d.value);
  const rawMax = Math.max(...vals, 0);
  const max = niceCeil(rawMax) || 1;
  const stepX = plotW / Math.max(series.length - 1, 1);
  const xOf = (i) => m.l + i * stepX;
  const yOf = (v) => m.t + plotH - (v / max) * plotH;
  const pts = series.map((d, i) => [xOf(i), yOf(d.value)]);

  // y-axis gridlines + labels (4 ticks).
  const TICKS = 4;
  for (let k = 0; k <= TICKS; k++) {
    const v = (max / TICKS) * k;
    const y = yOf(v);
    const grid = document.createElementNS(NS, "line");
    grid.setAttribute("x1", m.l); grid.setAttribute("x2", W - m.r);
    grid.setAttribute("y1", y.toFixed(1)); grid.setAttribute("y2", y.toFixed(1));
    grid.setAttribute("stroke", "#2e3a57"); grid.setAttribute("stroke-width", "1");
    if (k > 0) grid.setAttribute("stroke-dasharray", "3 3");
    svg.appendChild(grid);
    const lab = document.createElementNS(NS, "text");
    lab.setAttribute("x", m.l - 6); lab.setAttribute("y", (y + 3).toFixed(1));
    lab.setAttribute("text-anchor", "end"); lab.setAttribute("fill", "#9aa7c2");
    lab.setAttribute("font-size", "10"); lab.textContent = fmt(v);
    svg.appendChild(lab);
  }

  // x-axis date labels (first / middle / last to avoid crowding).
  const xIdx = series.length <= 2 ? [0, series.length - 1]
    : [0, Math.floor((series.length - 1) / 2), series.length - 1];
  for (const i of [...new Set(xIdx)]) {
    const lab = document.createElementNS(NS, "text");
    const anchor = i === 0 ? "start" : i === series.length - 1 ? "end" : "middle";
    lab.setAttribute("x", xOf(i).toFixed(1)); lab.setAttribute("y", H - 8);
    lab.setAttribute("text-anchor", anchor); lab.setAttribute("fill", "#9aa7c2");
    lab.setAttribute("font-size", "10"); lab.textContent = shortDate(series[i].bucket);
    svg.appendChild(lab);
  }

  // gradient area fill under the line.
  const gid = "npgrad-" + Math.random().toString(36).slice(2, 8);
  const defs = document.createElementNS(NS, "defs");
  const grad = document.createElementNS(NS, "linearGradient");
  grad.setAttribute("id", gid); grad.setAttribute("x1", "0"); grad.setAttribute("y1", "0");
  grad.setAttribute("x2", "0"); grad.setAttribute("y2", "1");
  const s0 = document.createElementNS(NS, "stop");
  s0.setAttribute("offset", "0"); s0.setAttribute("stop-color", color);
  s0.setAttribute("stop-opacity", "0.28");
  const s1 = document.createElementNS(NS, "stop");
  s1.setAttribute("offset", "1"); s1.setAttribute("stop-color", color);
  s1.setAttribute("stop-opacity", "0");
  grad.append(s0, s1); defs.appendChild(grad); svg.appendChild(defs);

  const lineD = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const areaD = `${lineD} L${pts[pts.length - 1][0].toFixed(1)},${(m.t + plotH).toFixed(1)} `
    + `L${pts[0][0].toFixed(1)},${(m.t + plotH).toFixed(1)} Z`;
  const area = document.createElementNS(NS, "path");
  area.setAttribute("d", areaD); area.setAttribute("fill", `url(#${gid})`);
  svg.appendChild(area);

  const line = document.createElementNS(NS, "path");
  line.setAttribute("d", lineD); line.setAttribute("fill", "none");
  line.setAttribute("stroke", color); line.setAttribute("stroke-width", "2");
  line.setAttribute("stroke-linejoin", "round"); line.setAttribute("stroke-linecap", "round");
  svg.appendChild(line);

  // hover layer: vertical guide + highlighted dot, mirrored by an HTML tooltip.
  const guide = document.createElementNS(NS, "line");
  guide.setAttribute("stroke", "#7e8aa6"); guide.setAttribute("stroke-width", "1");
  guide.setAttribute("stroke-dasharray", "3 3"); guide.setAttribute("opacity", "0");
  guide.setAttribute("y1", m.t); guide.setAttribute("y2", m.t + plotH);
  svg.appendChild(guide);
  const dot = document.createElementNS(NS, "circle");
  dot.setAttribute("r", "3.5"); dot.setAttribute("fill", color);
  dot.setAttribute("stroke", "#0f1320"); dot.setAttribute("stroke-width", "1.5");
  dot.setAttribute("opacity", "0"); svg.appendChild(dot);

  const tip = el("div", "npadmin-tip"); box.appendChild(tip);

  // Plot spans these fractions of the (scaled) viewBox width/height; map the
  // pointer through the same fractions so it tracks regardless of CSS width.
  const leftFrac = m.l / W, rightFrac = (W - m.r) / W;
  function onMove(ev) {
    const rect = svg.getBoundingClientRect();
    const fx = (ev.clientX - rect.left) / rect.width;
    let r = (fx - leftFrac) / (rightFrac - leftFrac);
    r = Math.max(0, Math.min(1, r));
    const i = Math.round(r * (series.length - 1));
    const [px, py] = pts[i];
    guide.setAttribute("x1", px); guide.setAttribute("x2", px);
    guide.setAttribute("opacity", "1");
    dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("opacity", "1");
    tip.innerHTML = "";
    tip.appendChild(el("div", "npadmin-tipv", fmt(series[i].value)));
    tip.appendChild(el("div", "npadmin-tipd", shortDate(series[i].bucket)));
    tip.style.left = (px / W * 100) + "%";
    tip.style.top = (py / H * 100) + "%";
    tip.classList.add("show");
  }
  function onLeave() {
    guide.setAttribute("opacity", "0"); dot.setAttribute("opacity", "0");
    tip.classList.remove("show");
  }
  svg.addEventListener("mousemove", onMove);
  svg.addEventListener("mouseleave", onLeave);
  return box;
}

// Round a max up to a clean axis bound so y-labels read nicely.
function niceCeil(v) {
  if (v <= 0) return 0;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / mag;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * mag;
}

// "2026-06-24T00:00:00+00:00" -> "Jun 24".
function shortDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return String(iso).slice(5, 10);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

async function tableByTopic(main) {
  const data = await api(`/by-topic${q()}`);
  const t = table(["Topic", "Sessions", "Escalation rate", "Avg msgs", "Cost"]);
  for (const r of data.topics) {
    addRow(t, [r.slug, r.sessions, pct(r.escalation_rate), r.avg_messages.toFixed(1), fmtUsd(r.cost_usd_total || 0)]);
  }
  sectionCol(main, "By topic", t);
}

async function tableByLanguage(main) {
  const data = await api(`/by-language${q()}`);
  const t = table(["Language", "Sessions", "Escalation rate", "Cost"]);
  for (const r of data.languages) addRow(t, [r.lang, r.sessions, pct(r.escalation_rate), fmtUsd(r.cost_usd_total || 0)]);
  sectionCol(main, "By language", t);
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------
async function viewSessions(main) {
  main.appendChild(el("h1", "npadmin-h", "Sessions"));
  main.appendChild(el("div", "npadmin-help", `Times shown in your timezone (${localTzLabel()}).`));
  const bar = dateToolbar(() => routeView(main));
  const search = el("input", "npadmin-input"); search.placeholder = "Search text…";
  search.style.width = "auto";
  const escSel = el("select", "npadmin-input"); escSel.style.width = "auto";
  for (const [v, l] of [["", "All"], ["true", "Escalated"], ["false", "Not escalated"]]) {
    const o = el("option", null, l); o.value = v; escSel.appendChild(o);
  }
  const go = el("button", "npadmin-btn", "Filter");

  // "Hide zero-message sessions" checkbox — on by default so empty greeting-only
  // sessions don't clutter the list.
  const hideEmptyCb = document.createElement("input");
  hideEmptyCb.type = "checkbox"; hideEmptyCb.checked = true;
  const hideEmptyLbl = el("label", "npadmin-hide-empty");
  hideEmptyLbl.append(hideEmptyCb, document.createTextNode(" Hide empty"));

  bar.append(search, escSel, go, hideEmptyLbl);
  main.appendChild(bar);
  const holder = el("div"); main.appendChild(holder);

  async function load(page = 1) {
    holder.innerHTML = "Loading…";
    let url = `/sessions${q()}&page=${page}`;
    if (search.value) url += `&q=${encodeURIComponent(search.value)}`;
    if (escSel.value) url += `&escalated=${escSel.value}`;
    if (hideEmptyCb.checked) url += `&min_messages=1`;
    try {
      const data = await api(url);
      holder.innerHTML = "";
      // The Product column matters only when several products are in view.
      const showProduct = !state.scope.productId;
      const heads = ["Created"];
      if (showProduct) heads.push("Product");
      heads.push("Topic", "Lang", "Status", "Msgs", "Cost", "");
      const t = table(heads);
      for (const s of data.items) {
        const cells = [fmtDateTime(s.created_at)];
        if (showProduct) cells.push(s.product_name || "—");
        cells.push(s.topic || "—", s.lang || "—",
          s.escalated ? "escalated" : s.status, s.message_count,
          fmtUsd(s.cost_usd_total || 0), "view →");
        const tr = addRow(t, cells);
        tr.classList.add("click");
        tr.addEventListener("click", () => openSession(s.id));
      }
      const scroll = el("div", "npadmin-table-scroll");
      scroll.appendChild(t);
      holder.appendChild(scroll);
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
  hideEmptyCb.addEventListener("change", () => load(1));
  load(1);
}

async function openSession(id) {
  // The URL becomes #sessions/ID, so keep state + sidebar highlight on Sessions
  // (a session detail is part of the Sessions section regardless of where the
  // click came from).
  state.view = "sessions";
  pushHash("sessions", id);
  syncNavActive();
  const main = document.getElementById("npadmin-main");
  main.innerHTML = "Loading…";
  try {
    const d = await api(`/session/${id}`);
    main.innerHTML = "";
    const back = el("button", "npadmin-btn ghost", "← Back");
    back.addEventListener("click", () => {
      state.view = "sessions";
      pushHash("sessions");
      syncNavActive();
      routeView(main);
    });
    main.appendChild(back);
    main.appendChild(el("h1", "npadmin-h", "Session " + id.slice(0, 8)));
    const meta = el("div", "npadmin-meta");
    meta.textContent = `status=${d.session.status} · escalated=${d.session.escalated}`
      + ` · lang=${d.session.lang || "—"} · cost=$${d.cost_usd_total}`
      + ` · created ${fmtDateTime(d.session.created_at)}`;
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
    // Long unbroken values (emails, ids) must wrap, not push the page sideways.
    pre.style.wordBreak = "break-word";
    pre.style.overflowX = "hidden";
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
    "Open or escalated sessions grouped by topic — same fields as the Sessions tab "
    + `so you can scan and pick the ones to handle. Times in your timezone (${localTzLabel()}).`));
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
      // Same columns as the Sessions tab (plus the first message) so a triager
      // can read and filter clusters without losing the session metadata.
      const t = table(["Created", "Lang", "Status", "Msgs", "Cost", "First message", "Session"]);
      for (const s of g.sessions) {
        const tr = addRow(t, [
          fmtDateTime(s.created_at), s.lang || "—",
          s.escalated ? "escalated" : s.status, s.message_count,
          fmtUsd(s.cost_usd_total || 0), s.first_message || "—",
          s.session_id.slice(0, 8)]);
        tr.classList.add("click");
        tr.addEventListener("click", () => openSession(s.session_id));
      }
      const scroll = el("div", "npadmin-table-scroll");
      scroll.appendChild(t);
      holder.appendChild(scroll);
    }
    if (!data.groups.length) holder.appendChild(el("div", "npadmin-meta", "Nothing unresolved 🎉"));
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Knowledge base — sub-tabs: Content (per-topic KB texts) | Variables (the
// {placeholder} registry used inside those texts; it belongs to the KB, so it
// lives here as a sub-view instead of a top-level tab).
// ---------------------------------------------------------------------------
async function viewKB(main, sub) {
  main.appendChild(el("h1", "npadmin-h", "Knowledge base"));
  const active = sub === "variables" ? "variables" : "content";
  subTabs(main, "kb", [["content", "Content"], ["variables", "Variables"]], active);
  main.appendChild(scopeHint());
  if (active === "variables") return kbVariablesView(main);
  main.appendChild(el("div", "npadmin-help",
    "One knowledge-base text per topic, injected into the prompt for that topic. "
    + "Edit a topic's text below and save. Values for {placeholder} tokens are "
    + "managed in the Variables sub-tab."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api(`/kb/topics${productQS(true)}`);
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
      if (!canWrite()) {
        // Managers are read-only: show the KB but no edit controls.
        ta.readOnly = true;
        holder.append(ta);
        continue;
      }
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
// Knowledge-base variables (the Variables sub-tab of the Knowledge base view)
// ---------------------------------------------------------------------------
async function kbVariablesView(main) {
  main.appendChild(el("div", "npadmin-help",
    "Admin-managed values for placeholders used inside knowledge-base texts. "
    + "When a KB answer contains a token like {min_deposit}, the prompt receives "
    + "the value from this registry. Current defaults come from the TEST column."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api(`/kb/variables${productQS(true)}`);
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
        valueInput.readOnly = !canWrite();
        valueTd.appendChild(valueInput);
        tr.appendChild(valueTd);
        const actionTd = el("td");
        const status = el("div", "npadmin-err");
        if (!canWrite()) { tr.appendChild(actionTd); t.querySelector("tbody").appendChild(tr); continue; }
        const save = el("button", "npadmin-btn", "Save");
        save.addEventListener("click", async () => {
          status.textContent = ""; status.style.color = "";
          try {
            const res = await api(
              `/kb/variables/${encodeURIComponent(v.key)}${productQS(true)}`, {
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
      const varScroll = el("div", "npadmin-table-scroll");
      varScroll.appendChild(t);
      tableWrap.appendChild(varScroll);
      tableWrap.appendChild(el("div", "npadmin-meta", `${rows.length} variables`));
    }

    filter.addEventListener("input", renderRows);
    renderRows();
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Prompt — sub-tabs: Preview (read-only assembled prompt) | Variables (the
// brand-uniquification values the prompt template renders with).
//
// The prompt WORDING is sourced solely from the server file `prompts.py` — the
// single source of truth — and is NOT editable from the admin; it is a dry
// template with {placeholders}. The Variables sub-tab edits the values that
// fill them (persona name, brand, products, tone of voice), plus the escalation
// keyword lists and the test player profile (both feed the same "tune the
// assistant per brand" workflow). To change the wording itself, edit prompts.py
// and redeploy.
// ---------------------------------------------------------------------------
async function viewPrompt(main, sub) {
  main.appendChild(el("h1", "npadmin-h", "Prompt"));
  const active = sub === "variables" ? "variables" : "preview";
  subTabs(main, "prompt",
          [["preview", "Preview"], ["variables", "Prompt variables"]], active);
  main.appendChild(scopeHint());
  if (active === "variables") return promptVariablesView(main);
  main.appendChild(el("div", "npadmin-help",
    "Read-only. The prompt wording lives in the server file prompts.py — the "
    + "single source of truth — and is not editable here. Below is the COMPLETE "
    + "prompt the model receives, assembled exactly as it's sent, with the "
    + "prompt variables already substituted. To change the values (brand, "
    + "persona, tone of voice), use the Prompt variables sub-tab; to change the "
    + "wording itself, edit prompts.py and redeploy. (Knowledge-base answers — "
    + "Layer 2 — are editable in the Knowledge base tab.)"));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api(`/effective-prompt${productQS(true)}`);
    holder.innerHTML = "";
    effectivePreviewBox(holder, data.effective_preview);
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Prompt variables (the Variables sub-tab of the Prompt view)
//
// Three blocks: (1) the {placeholder} values that uniquify the prompt template
// per brand; (2) the escalation keyword lists (pre-model triggers — strings the
// owner tunes alongside the prompt, mixed languages by design); (3) the test
// player profile (drives the Layer-3 player data in test/dev — moved here from
// the removed Test sandbox tab so it doesn't clutter the menu).
// ---------------------------------------------------------------------------
async function promptVariablesView(main) {
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const data = await api(`/prompt-variables${productQS(true)}`);
    holder.innerHTML = "";
    promptVariablesBox(holder, data.variables || []);
    await escalationKeywordsBox(holder);
    await testPlayerBox(holder);
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

function promptVariablesBox(holder, variables) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "Prompt variables — brand uniquification"));
  box.appendChild(el("div", "npadmin-help",
    "The prompt in prompts.py is a dry template; these values fill its "
    + "{placeholders} (persona name, brand, products, tone of voice) so the "
    + "assistant can be re-branded from here without touching the prompt file. "
    + "Values are English (the model-facing prompt stays English; the assistant "
    + "still answers in the player's language). Clear a field to fall back to "
    + "the built-in default. Applies to new requests immediately."));

  const fields = {};
  for (const v of variables) {
    const lab = el("label", "npadmin-field");
    lab.appendChild(el("span", null, `{${v.key}} — ${v.description}`));
    const inp = el("textarea", "npadmin-input");
    inp.value = v.value || "";
    inp.placeholder = v.default || "";
    inp.style.minHeight = (v.default || "").length > 120 ? "110px" : "40px";
    inp.readOnly = !canWrite();
    fields[v.key] = inp;
    lab.appendChild(inp);
    box.appendChild(lab);
  }

  if (canWrite()) {
    const err = el("div", "npadmin-err");
    const save = el("button", "npadmin-btn", "Save prompt variables");
    save.addEventListener("click", async () => {
      err.textContent = ""; err.style.color = "";
      const value = {};
      for (const [key, inp] of Object.entries(fields)) value[key] = inp.value;
      if (!confirm("Update the prompt variables now? Applies to new requests immediately.")) return;
      try {
        const res = await api(`/prompt-variables${productQS(true)}`,
                              { method: "PUT", body: { value } });
        // Re-fill with the resolved values (an emptied field shows its default again).
        for (const v of (res.variables || [])) {
          if (fields[v.key]) fields[v.key].value = v.value || "";
        }
        err.style.color = "var(--good)"; err.textContent = "Saved — live";
      } catch (e) { err.textContent = e.message; }
    });
    box.append(save, err);
  }
  holder.appendChild(box);
}

// The escalation keyword lists (soft pre-model triggers). They live in the
// `escalation` settings group; this block is a friendlier editor for the two
// lists (one keyword/stem per line) surfaced next to the prompt variables.
async function escalationKeywordsBox(holder) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "Escalation keywords"));
  box.appendChild(el("div", "npadmin-help",
    "Pre-model triggers: a message hitting one of these shows the contact card "
    + "without calling the model. One keyword, stem or phrase per line; stems "
    + "match at the start of a word. The lists are multilingual by design (they "
    + "scan the player's raw message, not the prompt). Related knobs elsewhere: "
    + "the per-session message cap is in Settings → general; the contact button "
    + "URL and copy are per-language in Translations."));
  try {
    const data = await api(`/settings${productQS(true)}`);
    const esc = (data.resolved || {}).escalation || {};
    const areas = {};
    for (const [key, label] of [
      ["high_risk_keywords", "High-risk keywords (fraud / legal — escalate immediately)"],
      ["human_request_keywords", "Explicit ask-for-a-human keywords"],
    ]) {
      const lab = el("label", "npadmin-field");
      lab.appendChild(el("span", null, label));
      const ta = el("textarea", "npadmin-input");
      ta.value = (esc[key] || []).join("\n");
      ta.style.minHeight = "140px";
      ta.readOnly = !canWrite();
      areas[key] = ta;
      lab.appendChild(ta);
      box.appendChild(lab);
    }
    if (canWrite()) {
      const err = el("div", "npadmin-err");
      const save = el("button", "npadmin-btn", "Save escalation keywords");
      save.addEventListener("click", async () => {
        err.textContent = ""; err.style.color = "";
        const toList = (ta) => ta.value.split("\n").map((s) => s.trim()).filter(Boolean);
        // The group holds only the two lists now — the technical message cap
        // lives in Settings → general.
        const value = {
          high_risk_keywords: toList(areas.high_risk_keywords),
          human_request_keywords: toList(areas.human_request_keywords),
        };
        if (!confirm("Update the escalation keywords now?")) return;
        try {
          await api(`/settings/escalation${productQS(true)}`,
                    { method: "PUT", body: { value } });
          err.style.color = "var(--good)"; err.textContent = "Saved — live";
        } catch (e) { err.textContent = e.message; }
      });
      box.append(save, err);
    }
  } catch (e) { box.appendChild(errBox(e)); }
  holder.appendChild(box);
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
async function viewSettings(main) {
  main.appendChild(el("h1", "npadmin-h", "Settings"));
  const productScoped = !!state.scope.productId;
  main.appendChild(el("div", "npadmin-help", productScoped
    ? "Per-PRODUCT overrides for the selected casino. Precedence: product > "
      + "global defaults > env. Saved values apply immediately."
    : "GLOBAL deploy defaults every product inherits (global admins only). "
      + "Select a product in the header to edit that casino's own overrides. "
      + "Saved values apply immediately."));
  if (productScoped) main.appendChild(scopeHint());
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    await ensureMeta();
    holder.innerHTML = "";
    const data = await api(`/settings${productQS(true)}`);
    for (const key of data.keys) {
      // The escalation keyword lists are content tuning, not technical
      // settings — they are edited in Prompt → Prompt variables (the technical
      // message cap lives in the `general` box below). No editor here, so the
      // same knob is never editable from two places.
      if (key === "escalation") continue;
      // The `retention` group has its home in Retention · Telegram → Settings;
      // skip it here so the same knobs aren't editable from two places.
      if (key === "retention") continue;
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
          await api(`/settings/${key}${productQS(true)}`,
                    { method: "PUT", body: { value: val } });
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
    + `${ex.lang || "—"}, player from the Test sandbox profile (anonymous when the `
    + `sandbox is disabled). Layer 2 (KB) and player data vary per request.`));

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

// Dedicated language-settings editor: a dropdown for the default answer language,
// checkboxes for the supported set, and an "add language" picker tied to the full
// ISO 639-1 catalogue — so a new language is always added with a valid code/name.
function languageSettingsBox(holder, current) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "language"));
  box.appendChild(el("div", "npadmin-help",
    "Enable the languages the assistant answers in. To add one that isn't listed, "
    + "pick it from the ISO 639-1 catalogue below — it appears as a new checkbox. "
    + "The default must be one of the enabled languages."));

  // Local mutable catalogue (built-ins + already-added + supported), keyed by code.
  const catalog = new Map();
  for (const l of (state.languages || [])) catalog.set(l.code, l.name);
  // Custom names to persist (everything the owner added beyond the built-ins);
  // seeded from what's already stored so previously-added languages round-trip.
  const customNames = { ...(current.names || {}) };
  const supported = new Set(current.supported || []);
  const checks = {};

  const defLab = el("label", "npadmin-field");
  defLab.appendChild(el("span", null, "Default answer language"));
  const defSel = el("select", "npadmin-input"); defSel.style.width = "auto";
  defLab.appendChild(defSel);
  box.appendChild(defLab);

  box.appendChild(el("div", "npadmin-meta", "Supported languages"));
  const checkRow = el("div", "npadmin-toolbar");
  checkRow.style.flexWrap = "wrap";
  box.appendChild(checkRow);

  function rebuildDefault() {
    const want = defSel.value || current.default || state.defaultLang;
    defSel.innerHTML = "";
    for (const [code, name] of catalog) {
      const o = el("option", null, `${name} (${code})`); o.value = code;
      if (code === want) o.selected = true;
      defSel.appendChild(o);
    }
  }
  function renderChecks() {
    checkRow.innerHTML = "";
    for (const [code, name] of catalog) {
      const lab = el("label", "npadmin-field");
      lab.style.flexDirection = "row"; lab.style.alignItems = "center";
      const cb = checks[code] || document.createElement("input");
      cb.type = "checkbox"; cb.checked = supported.has(code); checks[code] = cb;
      lab.append(cb, el("span", null, `${name} (${code})`));
      checkRow.appendChild(lab);
    }
  }
  rebuildDefault(); renderChecks();

  // Add-language picker: only ISO codes not already in the catalogue.
  box.appendChild(el("div", "npadmin-meta", "Add a language (ISO 639-1)"));
  const addRow = el("div", "npadmin-toolbar");
  const addSel = el("select", "npadmin-input"); addSel.style.width = "auto";
  function rebuildAddOptions() {
    addSel.innerHTML = "";
    const ph = el("option", null, "Select a language…"); ph.value = ""; addSel.appendChild(ph);
    for (const l of (state.isoCatalog || [])) {
      if (catalog.has(l.code)) continue;
      const o = el("option", null, `${l.name} (${l.code})`); o.value = l.code;
      addSel.appendChild(o);
    }
  }
  rebuildAddOptions();
  const addBtn = el("button", "npadmin-btn ghost", "Add language");
  addBtn.addEventListener("click", () => {
    const code = addSel.value;
    if (!code || catalog.has(code)) return;
    const found = (state.isoCatalog || []).find((l) => l.code === code);
    const name = found ? found.name : code.toUpperCase();
    catalog.set(code, name);
    customNames[code] = name;   // persist its name
    supported.add(code);        // newly added languages start enabled
    rebuildAddOptions(); renderChecks(); rebuildDefault();
  });
  addRow.append(addSel, addBtn);
  box.appendChild(addRow);

  const err = el("div", "npadmin-err");
  const save = el("button", "npadmin-btn", "Save language");
  save.addEventListener("click", async () => {
    err.textContent = ""; err.style.color = "";
    const sup = Object.entries(checks).filter(([, cb]) => cb.checked).map(([c]) => c);
    if (!sup.length) { err.textContent = "Select at least one supported language"; return; }
    if (!sup.includes(defSel.value)) { err.textContent = "Default must be a supported language"; return; }
    if (!confirm("Update 'language' settings now?")) return;
    try {
      await api(`/settings/language${productQS(true)}`, { method: "PUT",
        body: { value: { default: defSel.value, supported: sup, names: customNames } } });
      err.style.color = "var(--good)"; err.textContent = "Saved";
      state.languages = null; await ensureMeta();   // refresh the cached catalogue
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
  const buildLevel = (key, label, value, opts) => {
    const lab = el("label", "npadmin-field");
    lab.appendChild(el("span", null, label));
    const sel = el("select", "npadmin-input"); sel.style.width = "auto";
    for (const opt of opts) {
      const o = el("option", null, opt === "" ? "(model default)" : opt);
      o.value = opt;
      if ((value || "") === opt) o.selected = true;
      sel.appendChild(o);
    }
    selects[key] = sel;
    lab.appendChild(sel);
    box.appendChild(lab);
  };
  // "minimal" is the GPT-5 family's lowest reasoning tier (almost no hidden
  // reasoning) — valid for reasoning_effort only; verbosity stays low/medium/high
  // (the backend rejects "minimal" for verbosity, see settings.py validation).
  buildLevel("reasoning_effort", "Reasoning effort", current.reasoning_effort,
             ["", "minimal", "low", "medium", "high"]);
  buildLevel("verbosity", "Verbosity", current.verbosity,
             ["", "low", "medium", "high"]);

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
      await api(`/settings/model${productQS(true)}`, { method: "PUT", body: { value } });
      err.style.color = "var(--good)"; err.textContent = "Saved — live";
    } catch (e) { err.textContent = e.message; }
  });
  box.append(save, err);
  holder.appendChild(box);
}

// Dedicated "general" operational editor: session/admin-token lifetimes, the
// per-session message cap, the prompt history window, and the request body
// cap. These override the matching Railway env vars (SESSION_TTL_HOURS,
// ADMIN_TOKEN_TTL_MIN, MAX_MESSAGES_PER_SESSION, HISTORY_MAX_TURNS,
// BODY_MAX_BYTES) and apply without a redeploy. The escalation contact-button
// URL is NOT here any more — it's per-language now, edited in Translations.
function generalSettingsBox(holder, current) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "general — operational"));
  box.appendChild(el("div", "npadmin-help",
    "Technical limits and lifetimes. Overrides the matching Railway env vars; "
    + "applies live. (The escalation contact button URL is per-language — edit "
    + "it in the Translations tab.)"));

  const NUM = [
    ["session_ttl_hours", "Session TTL (hours)", "1"],
    ["admin_token_ttl_min", "Admin login TTL (minutes)", "5"],
    ["max_messages_per_session", "Max messages per session (then hand-off)", "1"],
    ["history_max_turns", "Prompt history window (turns sent to the model)", "1"],
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
    // PUT replaces the stored group; carry the legacy contact_form_url along
    // (it's the fallback for languages without a Translations contact_url), so
    // saving these knobs can't silently drop a previously stored URL.
    const value = current.contact_form_url
      ? { contact_form_url: current.contact_form_url } : {};
    for (const [key, label] of NUM) {
      value[key] = parseInt(fields[key].value, 10);
      if (Number.isNaN(value[key])) { err.textContent = `${label}: enter a number`; return; }
    }
    if (!confirm("Update 'general' settings now?")) return;
    try {
      await api(`/settings/general${productQS(true)}`, { method: "PUT", body: { value } });
      err.style.color = "var(--good)"; err.textContent = "Saved — live";
    } catch (e) { err.textContent = e.message; }
  });
  box.append(save, err);
  holder.appendChild(box);
}

// ---------------------------------------------------------------------------
// Structure — partners & their casino products (the multi-tenancy tree)
//
// Global admins create partners; partner admins add products (casinos) under
// their partner. Each product card shows its widget key + embed snippet and a
// write-only secrets form (OpenAI keys, handshake secret) — the server stores
// them encrypted and only ever reports has_* presence flags back.
// ---------------------------------------------------------------------------
async function viewStructure(main) {
  main.appendChild(el("h1", "npadmin-h", "Structure"));
  main.appendChild(el("div", "npadmin-help",
    "Partners own casino products. Each product is a fully separate tenant: its "
    + "own widget key, knowledge base, prompt variables, translations, settings "
    + "and OpenAI keys. Use the header switcher to work inside one product."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    await ensureStructure(true);
    holder.innerHTML = "";
    const isGlobalAdmin = state.globalRole === "admin";

    // --- create partner (global admins) ---------------------------------
    if (isGlobalAdmin) {
      const box = el("div", "npadmin-chart");
      box.appendChild(el("div", "npadmin-meta", "Add partner"));
      const slugInp = el("input", "npadmin-input"); slugInp.placeholder = "slug (a-z, 0-9, -)";
      const nameInp = el("input", "npadmin-input"); nameInp.placeholder = "Partner name";
      const err = el("div", "npadmin-err");
      const btn = el("button", "npadmin-btn", "Create partner");
      btn.addEventListener("click", async () => {
        err.textContent = "";
        try {
          await api("/partners", { method: "POST",
            body: { slug: slugInp.value.trim(), name: nameInp.value.trim() } });
          main.innerHTML = ""; viewStructure(main);
        } catch (e) { err.textContent = e.message; }
      });
      const row = el("div", "npadmin-formrow");
      row.append(slugInp, nameInp, btn);
      box.append(row, err);
      holder.appendChild(box);
    }

    for (const pa of (state.structure || [])) {
      const pbox = el("div", "npadmin-chart");
      const head = el("div", "npadmin-meta",
        `Partner: ${pa.name} (${pa.slug})${pa.active === false ? " — inactive" : ""}`);
      pbox.appendChild(head);
      const partnerAdmin = isGlobalAdmin || pa.role === "admin";

      if (isGlobalAdmin) {
        const row = el("div", "npadmin-formrow");
        const nameInp = el("input", "npadmin-input"); nameInp.value = pa.name;
        const actCb = document.createElement("input"); actCb.type = "checkbox";
        actCb.checked = pa.active !== false;
        const actLbl = el("label", "npadmin-hide-empty");
        actLbl.append(actCb, document.createTextNode(" Active"));
        const st = el("div", "npadmin-err");
        const save = el("button", "npadmin-btn ghost", "Save partner");
        save.addEventListener("click", async () => {
          st.textContent = ""; st.style.color = "";
          try {
            await api(`/partners/${pa.id}`, { method: "PUT",
              body: { name: nameInp.value.trim(), active: actCb.checked } });
            st.style.color = "var(--good)"; st.textContent = "Saved";
          } catch (e) { st.textContent = e.message; }
        });
        row.append(nameInp, actLbl, save, st);
        pbox.appendChild(row);
      }

      // --- products of this partner --------------------------------------
      for (const pr of (pa.products || [])) {
        pbox.appendChild(productCard(pr, main));
      }

      // --- add product (partner/global admins) ----------------------------
      if (partnerAdmin) {
        const row = el("div", "npadmin-formrow");
        const slugInp = el("input", "npadmin-input"); slugInp.placeholder = "slug";
        const nameInp = el("input", "npadmin-input"); nameInp.placeholder = "Casino name";
        const err = el("div", "npadmin-err");
        const btn = el("button", "npadmin-btn ghost", "+ Add product");
        btn.addEventListener("click", async () => {
          err.textContent = "";
          try {
            await api("/products", { method: "POST", body: {
              partner_id: pa.id, slug: slugInp.value.trim(), name: nameInp.value.trim() } });
            main.innerHTML = ""; viewStructure(main);
          } catch (e) { err.textContent = e.message; }
        });
        row.append(slugInp, nameInp, btn, err);
        pbox.appendChild(row);
      }
      holder.appendChild(pbox);
    }
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// One product card: name/active, widget key + embed snippet, secrets form.
function productCard(pr, main) {
  const card = el("div", "npadmin-productcard");
  card.appendChild(el("div", "npadmin-meta",
    `Product: ${pr.name} (${pr.slug})${pr.active === false ? " — inactive" : ""}`));

  const st = el("div", "npadmin-err");

  const row = el("div", "npadmin-formrow");
  const nameInp = el("input", "npadmin-input"); nameInp.value = pr.name;
  const actCb = document.createElement("input"); actCb.type = "checkbox";
  actCb.checked = pr.active !== false;
  const actLbl = el("label", "npadmin-hide-empty");
  actLbl.append(actCb, document.createTextNode(" Active"));
  const save = el("button", "npadmin-btn ghost", "Save");
  save.addEventListener("click", async () => {
    st.textContent = ""; st.style.color = "";
    try {
      await api(`/products/${pr.id}`, { method: "PUT",
        body: { name: nameInp.value.trim(), active: actCb.checked } });
      st.style.color = "var(--good)"; st.textContent = "Saved";
    } catch (e) { st.textContent = e.message; }
  });
  const openBtn = el("button", "npadmin-btn ghost", "Work in this product →");
  openBtn.addEventListener("click", () => {
    state.scope.partnerId = pr.partner_id; state.scope.productId = pr.id;
    state.languages = null; saveScope();
    const bar = document.getElementById("npadmin-scopebar");
    if (bar) renderScopeBar(bar, main);
    state.view = "kb"; state.param = null; pushHash("kb"); syncNavActive();
    routeView(main);
  });
  row.append(nameInp, actLbl, save, openBtn);
  card.appendChild(row);

  // Widget key + embed snippet: what the casino's site needs to connect.
  const keyLab = el("label", "npadmin-field");
  keyLab.appendChild(el("span", null, "Widget key (public product identifier)"));
  const keyRow = el("div", "npadmin-formrow");
  const keyInp = el("input", "npadmin-input"); keyInp.readOnly = true;
  keyInp.value = pr.widget_key || "";
  const copy = el("button", "npadmin-btn ghost", "Copy embed snippet");
  copy.addEventListener("click", () => {
    const origin = location.origin;
    const snippet =
      `<link rel="stylesheet" href="${origin}/widget.css">\n` +
      `<script type="module" src="${origin}/widget.js" ` +
      `data-widget-key="${pr.widget_key}"></script>`;
    navigator.clipboard.writeText(snippet).then(
      () => { st.style.color = "var(--good)"; st.textContent = "Embed snippet copied"; },
      () => { st.textContent = "Copy failed — copy the key manually"; });
  });
  const rotate = el("button", "npadmin-btn ghost", "Rotate key");
  rotate.addEventListener("click", async () => {
    if (!confirm("Rotate the widget key? Sites using the OLD key stop working "
                 + "until they embed the new one.")) return;
    st.textContent = ""; st.style.color = "";
    try {
      const res = await api(`/products/${pr.id}/widget-key`, { method: "POST" });
      keyInp.value = res.widget_key; pr.widget_key = res.widget_key;
      st.style.color = "var(--good)"; st.textContent = "Widget key rotated";
    } catch (e) { st.textContent = e.message; }
  });
  keyRow.append(keyInp, copy, rotate);
  keyLab.appendChild(keyRow);
  card.appendChild(keyLab);

  // Write-only secrets: OpenAI keys + handshake secret (stored encrypted). The
  // server never returns the values (only has_* presence flags), so each secret
  // shows a persistent "currently set / not set" status — visible even while you
  // type a replacement — and an explicit "Clear" toggle, instead of the old
  // hidden "type a space to wipe" gesture. Leaving a field blank keeps it as-is.
  const secHead = el("div", "npadmin-field");
  secHead.appendChild(el("span", null,
    "Product secrets (write-only; stored encrypted). Leave a field blank to keep "
    + "its current value; empty deploy falls back to the global env keys."));
  card.appendChild(secHead);

  const secrets = [];
  const mkSecret = (field, label, has) => {
    let isSet = has;
    const lab = el("label", "npadmin-field");
    const status = el("span");
    const setStatus = (on) => {
      status.textContent = label + " — ";
      status.appendChild(el("span", "npadmin-fieldstatus" + (on ? " set" : ""),
        on ? "currently set ✓" : "not set"));
    };
    setStatus(isSet);
    lab.appendChild(status);

    const rowEl = el("div", "npadmin-formrow");
    const inp = el("input", "npadmin-input"); inp.type = "password";
    inp.autocomplete = "new-password";
    inp.placeholder = isSet ? "Enter a new value to replace" : "Enter a value to set";
    const clrLbl = el("label", "npadmin-clearbox");
    const clr = document.createElement("input"); clr.type = "checkbox";
    clrLbl.append(clr, document.createTextNode(" Clear"));
    clrLbl.style.display = isSet ? "" : "none";   // nothing to clear until it's set
    clr.addEventListener("change", () => {
      inp.disabled = clr.checked;
      if (clr.checked) inp.value = "";
      inp.placeholder = clr.checked ? "Will be cleared on save"
        : (isSet ? "Enter a new value to replace" : "Enter a value to set");
    });
    rowEl.append(inp, clrLbl);
    lab.appendChild(rowEl);

    secrets.push({
      field,
      // A ticked Clear wipes the secret (sends ""); a typed value replaces it;
      // an untouched, empty field is left out so it keeps its current value.
      collect(body) {
        if (clr.checked) body[field] = "";
        else if (inp.value !== "") body[field] = inp.value;
      },
      refresh(nowSet) {
        isSet = nowSet; setStatus(nowSet);
        clr.checked = false; inp.disabled = false; inp.value = "";
        clrLbl.style.display = nowSet ? "" : "none";
        inp.placeholder = nowSet ? "Enter a new value to replace" : "Enter a value to set";
      },
    });
    return lab;
  };

  card.appendChild(mkSecret("openai_key_primary", "OpenAI key (primary)", pr.has_openai_key));
  card.appendChild(mkSecret("openai_key_fallback", "OpenAI key (fallback)", pr.has_openai_key_fallback));
  card.appendChild(mkSecret("handshake_secret", "Handshake secret", pr.has_handshake_secret));

  const saveSec = el("button", "npadmin-btn ghost", "Save secrets");
  saveSec.addEventListener("click", async () => {
    st.textContent = ""; st.style.color = "";
    const body = {};
    for (const s of secrets) s.collect(body);
    if (!Object.keys(body).length) { st.textContent = "Nothing to change"; return; }
    try {
      const res = await api(`/products/${pr.id}/secrets`, { method: "PUT", body });
      Object.assign(pr, res.product || {});
      // Reflect exactly what was just saved: a cleared field is now unset, a
      // replaced one is set; untouched secrets keep their status.
      for (const s of secrets) {
        if (s.field in body) s.refresh(body[s.field] !== "");
      }
      st.style.color = "var(--good)"; st.textContent = "Secrets saved (encrypted)";
    } catch (e) { st.textContent = e.message; }
  });
  card.appendChild(saveSec);

  card.appendChild(st);
  return card;
}

// ---------------------------------------------------------------------------
// Users — named accounts + scope memberships (admins only)
//
// Every admin signs in here; there is no password-only owner login. An account
// gets one role per SCOPE (global / partner / product): admin writes within the
// scope, manager is read-only. The server enforces reach — an admin only sees
// and manages accounts inside its own scopes.
// ---------------------------------------------------------------------------
// A scope + role selector (used by the create form and the grant form).
// Returns { node, spec() } where spec() -> {scope_type, partner_id, product_id, role}.
function scopeRoleSelector() {
  const wrap = el("div", "npadmin-formrow");
  const scopeSel = el("select", "npadmin-input");
  const scopes = [];
  if (state.globalRole === "admin") scopes.push(["global", "Global (whole hub)"]);
  scopes.push(["partner", "Partner (all its products)"]);
  scopes.push(["product", "Product (one casino)"]);
  for (const [v, l] of scopes) {
    const o = el("option", null, l); o.value = v; scopeSel.appendChild(o);
  }
  const paSel = el("select", "npadmin-input");
  for (const pa of (state.structure || [])) {
    const o = el("option", null, pa.name); o.value = String(pa.id); paSel.appendChild(o);
  }
  const prSel = el("select", "npadmin-input");
  for (const pr of allProducts()) {
    const o = el("option", null, pr.name); o.value = String(pr.id); prSel.appendChild(o);
  }
  const roleSel = el("select", "npadmin-input");
  for (const r of ["manager", "admin"]) {
    const o = el("option", null, r); o.value = r; roleSel.appendChild(o);
  }
  function sync() {
    paSel.style.display = scopeSel.value === "partner" ? "" : "none";
    prSel.style.display = scopeSel.value === "product" ? "" : "none";
  }
  scopeSel.addEventListener("change", sync);
  scopeSel.value = scopes[0][0]; sync();
  wrap.append(scopeSel, paSel, prSel, roleSel);
  return {
    node: wrap,
    spec() {
      return {
        scope_type: scopeSel.value,
        partner_id: scopeSel.value === "partner" ? parseInt(paSel.value, 10) : null,
        product_id: scopeSel.value === "product" ? parseInt(prSel.value, 10) : null,
        role: roleSel.value,
      };
    },
  };
}

function membershipLabel(m) {
  if (m.scope_type === "global") return `global · ${m.role}`;
  if (m.scope_type === "partner")
    return `partner ${m.partner_name || m.partner_id} · ${m.role}`;
  return `product ${m.product_name || m.product_id} · ${m.role}`;
}

async function viewUsers(main) {
  main.appendChild(el("h1", "npadmin-h", "Users"));
  main.appendChild(el("div", "npadmin-help",
    "Named login accounts (email + password) with one role per SCOPE: global "
    + "(the whole hub), a partner (all its casinos) or a single product. Admin "
    + "writes within the scope; manager is read-only there. You only see and "
    + "manage accounts inside your own reach. Keep at least two global admin "
    + "accounts — there is no password recovery. No emails are sent — you "
    + "manage passwords here."));
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    await ensureStructure();
    const data = await api("/users");
    holder.innerHTML = "";

    // Create form
    const createBox = el("div", "npadmin-chart");
    createBox.appendChild(el("div", "npadmin-meta", "Add user"));
    const emailLab = el("label", "npadmin-field");
    emailLab.appendChild(el("span", null, "Email"));
    const emailInp = el("input", "npadmin-input"); emailInp.type = "email";
    emailInp.placeholder = "person@example.com"; emailLab.appendChild(emailInp);
    const pwLab = el("label", "npadmin-field");
    pwLab.appendChild(el("span", null, "Password (min 8 characters)"));
    const pwInp = el("input", "npadmin-input"); pwInp.type = "text";
    pwInp.placeholder = "Set an initial password"; pwLab.appendChild(pwInp);
    const scopeLab = el("label", "npadmin-field");
    scopeLab.appendChild(el("span", null, "Initial access (scope + role)"));
    const scopeSel = scopeRoleSelector();
    scopeLab.appendChild(scopeSel.node);
    const cErr = el("div", "npadmin-err");
    const createBtn = el("button", "npadmin-btn", "Create user");
    createBtn.addEventListener("click", async () => {
      cErr.textContent = ""; cErr.style.color = "";
      const spec = scopeSel.spec();
      try {
        await api("/users", { method: "POST", body: {
          email: emailInp.value.trim(), password: pwInp.value,
          role: spec.role, scope_type: spec.scope_type,
          partner_id: spec.partner_id, product_id: spec.product_id } });
        cErr.style.color = "var(--good)"; cErr.textContent = "Created";
        emailInp.value = ""; pwInp.value = "";
        // Re-render from scratch: viewUsers APPENDS to main (routeView normally
        // clears it first), so without this the whole tab stacks a second copy.
        main.innerHTML = "";
        viewUsers(main);
      } catch (e) { cErr.textContent = e.message; }
    });
    createBox.append(emailLab, pwLab, scopeLab, createBtn, cErr);
    holder.appendChild(createBox);

    // Existing users
    const listBox = el("div", "npadmin-chart");
    listBox.appendChild(el("div", "npadmin-meta", "Existing users"));
    if (!(data.users || []).length) {
      listBox.appendChild(el("div", "npadmin-meta",
        "No users yet."));
    }
    const t = table(["Email", "Access", "Active", "Created", "Actions"]);
    for (const u of (data.users || [])) {
      const isSelf = u.email === state.email;
      const tr = el("tr");
      tr.appendChild(el("td", null, u.email + (isSelf ? " (you)" : "")));
      const status = el("div", "npadmin-err");

      // memberships: chips with revoke + a grant row
      const memTd = el("td");
      const chips = el("div", "npadmin-toolbar");
      chips.style.marginBottom = "4px";
      for (const m of (u.memberships || [])) {
        const chip = el("span", "npadmin-chipm", membershipLabel(m));
        if (!isSelf) {
          const x = el("button", "npadmin-chipx", "×");
          x.title = "Revoke this access";
          x.addEventListener("click", async () => {
            if (!confirm(`Revoke "${membershipLabel(m)}" from ${u.email}?`)) return;
            status.textContent = ""; status.style.color = "";
            try {
              await api(`/users/${encodeURIComponent(u.email)}/memberships/${m.id}`,
                        { method: "DELETE" });
              main.innerHTML = ""; viewUsers(main);
            } catch (e) { status.textContent = e.message; }
          });
          chip.appendChild(x);
        }
        chips.appendChild(chip);
      }
      memTd.appendChild(chips);
      if (!isSelf) {
        const grant = scopeRoleSelector();
        const gBtn = el("button", "npadmin-btn ghost", "Grant");
        gBtn.addEventListener("click", async () => {
          status.textContent = ""; status.style.color = "";
          try {
            await api(`/users/${encodeURIComponent(u.email)}/memberships`,
                      { method: "POST", body: grant.spec() });
            main.innerHTML = ""; viewUsers(main);
          } catch (e) { status.textContent = e.message; }
        });
        grant.node.appendChild(gBtn);
        memTd.appendChild(grant.node);
      }
      tr.appendChild(memTd);

      // active toggle
      const actTd = el("td");
      const actCb = document.createElement("input"); actCb.type = "checkbox";
      actCb.checked = u.active !== false;
      actCb.addEventListener("change", async () => {
        status.textContent = ""; status.style.color = "";
        try {
          await api(`/users/${encodeURIComponent(u.email)}`, { method: "PUT",
            body: { active: actCb.checked } });
          status.style.color = "var(--good)"; status.textContent = "Saved";
        } catch (e) { status.textContent = e.message; actCb.checked = !actCb.checked; }
      });
      actTd.appendChild(actCb); tr.appendChild(actTd);
      tr.appendChild(el("td", null, fmtDateTime(u.created_at)));

      // actions
      const opTd = el("td");
      const pwB = el("button", "npadmin-btn ghost", "Set password");
      pwB.addEventListener("click", async () => {
        const np = prompt(`New password for ${u.email} (min 8 characters):`);
        if (!np) return;
        status.textContent = ""; status.style.color = "";
        try {
          await api(`/users/${encodeURIComponent(u.email)}`, { method: "PUT",
            body: { password: np } });
          status.style.color = "var(--good)"; status.textContent = "Password updated";
        } catch (e) { status.textContent = e.message; }
      });
      const delB = el("button", "npadmin-btn ghost", "Delete");
      delB.addEventListener("click", async () => {
        if (!confirm(`Delete user ${u.email}?`)) return;
        try {
          await api(`/users/${encodeURIComponent(u.email)}`, { method: "DELETE" });
          main.innerHTML = "";
          viewUsers(main);
        } catch (e) { status.textContent = e.message; }
      });
      const ops = el("div", "npadmin-toolbar");
      ops.append(pwB, delB);
      opTd.append(ops, status); tr.appendChild(opTd);
      t.querySelector("tbody").appendChild(tr);
    }
    const scroll = el("div", "npadmin-table-scroll");
    scroll.appendChild(t); listBox.appendChild(scroll);
    holder.appendChild(listBox);
  } catch (e) { holder.innerHTML = ""; holder.appendChild(errBox(e)); }
}

// ---------------------------------------------------------------------------
// Test player — the stand-in player profile for the test widget (test page /).
// Lives as a block in the Prompt variables sub-tab (it exists to test the
// prompt's personalization, so it belongs with the prompt knobs, not the menu).
//
// In production the host site supplies user_context over a signed handshake;
// in test/dev this stored profile stands in for it. It feeds Layer 3 of the
// prompt (so the model can greet the player by name).
// ---------------------------------------------------------------------------
async function testPlayerBox(holder) {
  const box = el("div", "npadmin-chart");
  box.appendChild(el("div", "npadmin-meta", "Test player (sandbox)"));
  box.appendChild(el("div", "npadmin-help",
    "The player profile used by the test widget (test page at /) to test the "
    + "prompt — e.g. name personalization. In production the host site supplies "
    + "this over a signed handshake; here it stands in for it. These fields feed "
    + "Layer 3 of the prompt. The session language always follows the browser."));
  try {
    const data = await api(`/test-profile${productQS(true)}`);
    const p = data.profile || {};

    if (!data.active) {
      box.appendChild(el("div", "npadmin-warnbox",
        "A handshake secret is configured (for this product or deploy-wide), so "
        + "the host site is authoritative and this test profile is ignored at "
        + "session create."));
    }

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
      inp.readOnly = !canWrite();
      fields[key] = inp; lab.appendChild(inp);
      box.appendChild(lab);
    }
    en.disabled = !canWrite();

    const actions = el("div", "npadmin-toolbar");
    const err = el("div", "npadmin-err");
    if (canWrite()) {
      const save = el("button", "npadmin-btn", "Save test player");
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
          await api(`/test-profile${productQS(true)}`, { method: "PUT", body: { value } });
          err.style.color = "var(--good)";
          err.textContent = "Saved — applies to the next chat session (reopen the widget)";
        } catch (e) { err.textContent = e.message; }
      });
      actions.append(save);
    }
    const open = el("a", "npadmin-btn ghost", "Open test page ↗");
    open.href = "/"; open.target = "_blank"; open.style.marginLeft = "8px";
    actions.append(open);
    box.append(actions, err);
  } catch (e) { box.appendChild(errBox(e)); }
  holder.appendChild(box);
}

// ---------------------------------------------------------------------------
// Translations — every user-facing string in the widget, per language
//
// The widget chrome (title, greeting, buttons, errors) and the server-generated
// turns (escalation card, closing bubble, nudges) resolve through the server
// translations registry: admin overrides here > the built-in defaults. Topic
// titles (the picker buttons) are stored per-language on the topics themselves,
// so they are edited here too.
// ---------------------------------------------------------------------------
async function viewTranslations(main) {
  main.appendChild(el("h1", "npadmin-h", "Translations"));
  main.appendChild(el("div", "npadmin-help",
    "Everything the player sees in the widget, editable per language: the "
    + "widget texts (title, greeting, buttons, errors), the assistant's service "
    + "replies (escalation card, its contact button URL, closing option, "
    + "nudges) and the topic names. Empty fields fall back to the built-in "
    + "copy (then English; the contact URL falls back to the deploy default). "
    + "The admin panel itself stays English."));
  main.appendChild(scopeHint());
  const holder = el("div", null, "Loading…"); main.appendChild(holder);
  try {
    const [data, topicsData] = await Promise.all([
      api(`/translations${productQS(true)}`), api(`/kb/topics${productQS(true)}`),
    ]);
    holder.innerHTML = "";

    const languages = data.languages || [];
    const overrides = data.overrides || {};
    let lang = (languages.find((l) => l.code === state.defaultLang) || languages[0] || {}).code;
    if (!lang) { holder.appendChild(el("div", "npadmin-meta", "No supported languages configured.")); return; }

    // Language picker (the supported set; add languages in Settings → language).
    const bar = el("div", "npadmin-toolbar");
    bar.appendChild(el("span", "npadmin-meta", "Language"));
    const sel = el("select", "npadmin-input"); sel.style.width = "auto";
    for (const l of languages) {
      const o = el("option", null, `${l.name} (${l.code})`); o.value = l.code;
      if (l.code === lang) o.selected = true;
      sel.appendChild(o);
    }
    bar.appendChild(sel);
    holder.appendChild(bar);

    const body = el("div"); holder.appendChild(body);

    function renderLang() {
      body.innerHTML = "";
      const resolved = (data.resolved || {})[lang] || {};
      const defaults = (data.defaults || {})[lang] || {};

      // --- copy strings, grouped by scope --------------------------------
      const groups = [
        ["widget", "Widget texts",
         "Chrome strings rendered by the widget itself."],
        ["server", "Assistant service replies",
         "Model-free texts the server sends as part of a turn."],
      ];
      const inputs = {};
      for (const [scope, title, help] of groups) {
        const box = el("div", "npadmin-chart");
        box.appendChild(el("div", "npadmin-meta", title));
        box.appendChild(el("div", "npadmin-help", help));
        for (const k of (data.keys || [])) {
          if (k.scope !== scope) continue;
          const lab = el("label", "npadmin-field");
          lab.appendChild(el("span", null, k.description));
          const inp = el("textarea", "npadmin-input");
          inp.value = resolved[k.key] || "";
          inp.placeholder = defaults[k.key] || "";
          inp.style.minHeight = "40px";
          inp.readOnly = !canWrite();
          inputs[k.key] = inp;
          lab.appendChild(inp);
          box.appendChild(lab);
        }
        body.appendChild(box);
      }

      if (canWrite()) {
        const err = el("div", "npadmin-err");
        const save = el("button", "npadmin-btn", `Save texts (${lang})`);
        save.addEventListener("click", async () => {
          err.textContent = ""; err.style.color = "";
          // Store only values that differ from the built-in default — defaults
          // keep flowing through for everything untouched.
          const edited = {};
          for (const [key, inp] of Object.entries(inputs)) {
            const v = inp.value;
            if (v.trim() && v !== (defaults[key] || "")) edited[key] = v;
          }
          const value = { ...overrides };
          if (Object.keys(edited).length) value[lang] = edited;
          else delete value[lang];
          try {
            const res = await api(`/translations${productQS(true)}`,
                                  { method: "PUT", body: { value } });
            data.resolved = res.resolved || data.resolved;
            Object.keys(overrides).forEach((k) => delete overrides[k]);
            Object.assign(overrides, res.overrides || {});
            err.style.color = "var(--good)"; err.textContent = "Saved — live";
          } catch (e) { err.textContent = e.message; }
        });
        const saveBar = el("div", "npadmin-toolbar");
        saveBar.append(save, err);
        body.appendChild(saveBar);
      }

      // --- topic titles ---------------------------------------------------
      const tbox = el("div", "npadmin-chart");
      tbox.appendChild(el("div", "npadmin-meta", "Topic names"));
      tbox.appendChild(el("div", "npadmin-help",
        "The topic picker buttons, per language. Stored on the topic itself; "
        + "a missing translation falls back to English."));
      for (const topic of (topicsData.topics || [])) {
        const lab = el("label", "npadmin-field");
        lab.appendChild(el("span", null,
          `${topic.slug} (en: ${topic.title.en || "—"})`));
        const inp = el("input", "npadmin-input");
        inp.value = topic.title[lang] || "";
        inp.placeholder = topic.title.en || "";
        inp.readOnly = !canWrite();
        lab.appendChild(inp);
        if (canWrite()) {
          const row = el("div", "npadmin-toolbar");
          const status = el("div", "npadmin-err");
          const save = el("button", "npadmin-btn ghost", "Save");
          save.addEventListener("click", async () => {
            status.textContent = ""; status.style.color = "";
            const title = { ...topic.title };
            if (inp.value.trim()) title[lang] = inp.value.trim();
            else delete title[lang];
            try {
              await api("/kb/topics", { method: "POST", body: {
                slug: topic.slug, title,
                order: topic.display_order || 0,
                active: topic.active !== false,
                product_id: state.scope.productId || topic.product_id || null,
              } });
              topic.title = title;
              status.style.color = "var(--good)"; status.textContent = "Saved";
            } catch (e) { status.textContent = e.message; }
          });
          row.append(save, status);
          lab.appendChild(row);
        }
        tbox.appendChild(lab);
      }
      body.appendChild(tbox);
    }

    sel.addEventListener("change", () => { lang = sel.value; renderLang(); });
    renderLang();
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
// Render a server ISO timestamp (UTC, tz-aware) in the VIEWER's local timezone —
// the admin sits in their own zone, so 06:00Z shows as 09:00 for a UTC+3 viewer.
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}
// The viewer's IANA timezone name, for a small "times shown in …" hint.
function localTzLabel() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "local time"; }
  catch (_) { return "local time"; }
}
function errBox(e) { return el("div", "npadmin-warnbox", e.message || String(e)); }
function section(main, title, node) {
  main.appendChild(el("h3", null, title)); main.appendChild(node);
}
// One grid cell (title + node) for the two-up .npadmin-2col layout.
// Tables are wrapped in a scroll container so they don't break mobile layout.
function sectionCol(parent, title, node) {
  const col = el("div");
  col.appendChild(el("h3", null, title));
  if (node.tagName === "TABLE") {
    const scroll = el("div", "npadmin-table-scroll");
    scroll.appendChild(node);
    col.appendChild(scroll);
  } else {
    col.appendChild(node);
  }
  parent.appendChild(col);
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

// ===========================================================================
// Retention · Telegram (Section B) — one view with sub-tabs. Everything is
// product-scoped: edits act on the selected product (else the default). The
// `retention` settings GROUP is edited under the Settings sub-tab (it reuses
// the generic /admin/settings/retention endpoint).
// ===========================================================================
const RETENTION_SUBTABS = [
  ["telegram", "Telegram config"], ["kb", "Retention KB"], ["media", "Media"],
  ["managers", "Managers"], ["config", "Settings"], ["analytics", "Analytics"],
];

function viewRetention(main, param) {
  const sub = RETENTION_SUBTABS.some(([id]) => id === param) ? param : "telegram";
  main.appendChild(el("h1", "npadmin-h", "Retention · Telegram"));
  subTabs(main, "retention", RETENTION_SUBTABS, sub);
  main.appendChild(scopeHint());
  const pr = editedProduct();
  if (!pr) {
    main.appendChild(el("div", "npadmin-warnbox",
      "No product resolved. Create a product in Structure first."));
    return;
  }
  const pid = pr.id;
  const body = el("div"); main.appendChild(body);
  ({
    telegram: retTelegram, kb: retKB, media: retMedia,
    managers: retManagers, config: retConfig, analytics: retAnalytics,
  }[sub])(body, pid, pr);
}

// --- Telegram config + secrets + webhook -----------------------------------
async function retTelegram(main, pid) {
  const st = el("div", "npadmin-err"); main.appendChild(st);
  main.appendChild(el("div", "npadmin-meta", "Loading…"));
  let data;
  try { data = await api(`/retention/telegram/${pid}`); }
  catch (e) { main.appendChild(errBox(e)); return; }
  main.innerHTML = ""; main.appendChild(st);
  const p = data.product;
  const wr = canWrite();

  // Enable + config fields
  const cfg = el("div", "npadmin-productcard");
  cfg.appendChild(el("div", "npadmin-meta", "Bot & channel configuration"));
  const enCb = document.createElement("input"); enCb.type = "checkbox";
  enCb.checked = !!p.retention_enabled; enCb.disabled = !wr;
  const enLbl = el("label", "npadmin-field");
  enLbl.append(enCb, document.createTextNode(" Retention bot enabled"));
  cfg.appendChild(enLbl);
  const mk = (label, val, ph) => {
    const l = el("label", "npadmin-field"); l.appendChild(el("span", null, label));
    const i = el("input", "npadmin-input"); i.value = val || ""; i.placeholder = ph || "";
    i.disabled = !wr; l.appendChild(i); cfg.appendChild(l); return i;
  };
  const uInp = mk("Bot username (without @)", p.telegram_bot_username, "nika_casino_bot");
  const chIdInp = mk("Channel id (@channel or -100…)", p.telegram_channel_id, "@my_channel");
  const chUrlInp = mk("Channel URL", p.telegram_channel_url, "https://t.me/my_channel");
  const apiUrlInp = mk("Player API URL (profile pull)", p.player_api_url, "https://casino.example/api/player");
  if (wr) {
    const save = el("button", "npadmin-btn", "Save config");
    save.addEventListener("click", async () => {
      st.textContent = ""; st.style.color = "";
      try {
        await api(`/retention/telegram/${pid}`, { method: "PUT", body: {
          retention_enabled: enCb.checked,
          telegram_bot_username: uInp.value.trim(),
          telegram_channel_id: chIdInp.value.trim(),
          telegram_channel_url: chUrlInp.value.trim(),
          player_api_url: apiUrlInp.value.trim(),
        } });
        st.style.color = "var(--good)"; st.textContent = "Saved";
      } catch (e) { st.textContent = e.message; }
    });
    cfg.appendChild(save);
  }
  main.appendChild(cfg);

  // Secrets (write-only): bot token + player API key
  const sec = el("div", "npadmin-productcard");
  sec.appendChild(el("div", "npadmin-meta",
    "Secrets (write-only, stored encrypted). Leave blank to keep current."));
  const mkSecret = (field, label, has) => {
    const l = el("label", "npadmin-field");
    l.appendChild(el("span", null, `${label} — ${has ? "currently set ✓" : "not set"}`));
    const i = el("input", "npadmin-input"); i.type = "password";
    i.autocomplete = "new-password"; i.disabled = !wr;
    i.placeholder = has ? "Enter new value to replace" : "Enter a value to set";
    l.appendChild(i); sec.appendChild(l);
    return { field, get: () => i.value };
  };
  const tok = mkSecret("telegram_bot_token", "Telegram bot token", p.has_telegram_bot_token);
  const key = mkSecret("player_api_key", "Player API key", p.has_player_api_key);
  if (wr) {
    const save = el("button", "npadmin-btn", "Save secrets");
    save.addEventListener("click", async () => {
      const b = {};
      if (tok.get() !== "") b.telegram_bot_token = tok.get();
      if (key.get() !== "") b.player_api_key = key.get();
      if (!Object.keys(b).length) { st.textContent = "Nothing to save"; return; }
      st.textContent = ""; st.style.color = "";
      try {
        await api(`/products/${pid}/secrets`, { method: "PUT", body: b });
        st.style.color = "var(--good)"; st.textContent = "Secrets saved";
        retTelegram(main, pid); // refresh has_* flags + webhook url
      } catch (e) { st.textContent = e.message; }
    });
    sec.appendChild(save);
  }
  main.appendChild(sec);

  // Webhook
  const wh = el("div", "npadmin-productcard");
  wh.appendChild(el("div", "npadmin-meta", "Telegram webhook"));
  const whUrl = el("input", "npadmin-input"); whUrl.readOnly = true;
  whUrl.value = data.webhook_url || "(set PUBLIC_BASE_URL + bot token, then it appears)";
  wh.appendChild(whUrl);
  if (wr) {
    const reg = el("button", "npadmin-btn", "Register / refresh webhook with Telegram");
    reg.addEventListener("click", async () => {
      st.textContent = "Registering…"; st.style.color = "";
      try {
        const r = await api(`/retention/webhook/${pid}`, { method: "POST" });
        st.style.color = "var(--good)";
        st.textContent = r.ok ? `Webhook set (bot @${r.bot || "?"})` : "Telegram rejected the webhook";
      } catch (e) { st.textContent = e.message; }
    });
    wh.appendChild(reg);
  }
  main.appendChild(wh);
}

// --- Retention KB ----------------------------------------------------------
async function retKB(main, pid) {
  const st = el("div", "npadmin-err"); main.appendChild(st);
  const listWrap = el("div"); main.appendChild(listWrap);
  const wr = canWrite();
  async function reload() {
    listWrap.innerHTML = "";
    let items;
    try { items = (await api(`/retention/kb?product_id=${pid}`)).items; }
    catch (e) { listWrap.appendChild(errBox(e)); return; }
    if (!items.length) listWrap.appendChild(el("div", "npadmin-meta", "No scenarios yet."));
    for (const it of items) listWrap.appendChild(kbEntryCard(it, wr, pid, reload, st));
  }
  if (wr) {
    const add = el("div", "npadmin-productcard");
    add.appendChild(el("div", "npadmin-meta", "Add scenario / offer"));
    const title = el("input", "npadmin-input"); title.placeholder = "Title (e.g. Long time no see)";
    const when = el("input", "npadmin-input"); when.placeholder = "Trigger / when (optional)";
    const bodyT = el("textarea", "npadmin-input"); bodyT.rows = 3;
    bodyT.placeholder = "What Nika should say / offer";
    const links = el("input", "npadmin-input"); links.placeholder = "Links (comma-separated, optional)";
    const btn = el("button", "npadmin-btn", "Add");
    btn.addEventListener("click", async () => {
      if (!title.value.trim() || !bodyT.value.trim()) { st.textContent = "Title and body required"; return; }
      st.textContent = "";
      try {
        await api("/retention/kb", { method: "POST", body: {
          product_id: pid, title: title.value.trim(),
          trigger_when: when.value.trim(), body: bodyT.value.trim(),
          links: links.value.split(",").map((s) => s.trim()).filter(Boolean),
        } });
        title.value = when.value = bodyT.value = links.value = "";
        reload();
      } catch (e) { st.textContent = e.message; }
    });
    add.append(title, when, bodyT, links, btn);
    main.appendChild(add);
  }
  await reload();
}

function kbEntryCard(it, wr, pid, reload, st) {
  const card = el("div", "npadmin-productcard");
  const title = el("input", "npadmin-input"); title.value = it.title; title.disabled = !wr;
  const when = el("input", "npadmin-input"); when.value = it.trigger_when || ""; when.disabled = !wr;
  when.placeholder = "Trigger / when";
  const bodyT = el("textarea", "npadmin-input"); bodyT.rows = 3; bodyT.value = it.body; bodyT.disabled = !wr;
  const links = el("input", "npadmin-input"); links.value = (it.links || []).join(", "); links.disabled = !wr;
  links.placeholder = "Links";
  card.append(title, when, bodyT, links);
  if (wr) {
    const row = el("div", "npadmin-formrow");
    const save = el("button", "npadmin-btn ghost", "Save");
    save.addEventListener("click", async () => {
      try {
        await api(`/retention/kb/${it.id}`, { method: "PUT", body: {
          title: title.value.trim(), trigger_when: when.value.trim(),
          body: bodyT.value.trim(),
          links: links.value.split(",").map((s) => s.trim()).filter(Boolean),
          sort_order: it.sort_order, active: it.active,
        } });
        st.style.color = "var(--good)"; st.textContent = "Saved";
      } catch (e) { st.textContent = e.message; }
    });
    const del = el("button", "npadmin-btn ghost", "Delete");
    del.addEventListener("click", async () => {
      if (!confirm("Delete this scenario?")) return;
      try { await api(`/retention/kb/${it.id}`, { method: "DELETE" }); reload(); }
      catch (e) { st.textContent = e.message; }
    });
    row.append(save, del); card.appendChild(row);
  }
  return card;
}

// --- Media library ---------------------------------------------------------
async function retMedia(main, pid) {
  const st = el("div", "npadmin-err"); main.appendChild(st);
  const grid = el("div");
  const wr = canWrite();
  if (wr) {
    const add = el("div", "npadmin-productcard");
    add.appendChild(el("div", "npadmin-meta", "Upload photo"));
    const file = document.createElement("input"); file.type = "file"; file.accept = "image/*";
    const desc = el("textarea", "npadmin-input"); desc.rows = 2;
    desc.placeholder = "Internal description (what's on the photo) — the model reads this";
    const tags = el("input", "npadmin-input"); tags.placeholder = "Tags (comma-separated)";
    const lvl = el("input", "npadmin-input"); lvl.type = "number"; lvl.value = "0"; lvl.placeholder = "level_min (VIP tier ordinal)";
    const stg = el("input", "npadmin-input"); stg.type = "number"; stg.value = "1"; stg.placeholder = "stage";
    const cat = el("input", "npadmin-input"); cat.placeholder = "Category (optional)";
    const btn = el("button", "npadmin-btn", "Upload");
    btn.addEventListener("click", async () => {
      if (!file.files.length) { st.textContent = "Choose a file"; return; }
      const fd = new FormData();
      fd.append("product_id", pid); fd.append("description", desc.value);
      fd.append("tags", tags.value); fd.append("level_min", lvl.value || "0");
      fd.append("stage", stg.value || "1"); fd.append("category", cat.value);
      fd.append("file", file.files[0]);
      st.textContent = "Uploading…"; st.style.color = "";
      try { await api("/retention/photos", { method: "POST", form: fd }); reload(); file.value = ""; desc.value = tags.value = cat.value = ""; }
      catch (e) { st.textContent = e.message; }
    });
    const lblFor = (t, node) => { const l = el("label", "npadmin-field"); l.appendChild(el("span", null, t)); l.appendChild(node); return l; };
    add.append(lblFor("Image", file), desc, tags,
      lblFor("Min VIP tier (level_min)", lvl), lblFor("Explicitness stage", stg), cat, btn);
    main.appendChild(add);
  }
  main.appendChild(grid);
  async function reload() {
    grid.innerHTML = "";
    let items;
    try { items = (await api(`/retention/photos?product_id=${pid}`)).items; }
    catch (e) { grid.appendChild(errBox(e)); return; }
    grid.appendChild(el("div", "npadmin-meta", `${items.length} photo(s)`));
    const wrap = el("div", "npadmin-cards");
    for (const ph of items) wrap.appendChild(photoCard(ph, wr, reload, st));
    grid.appendChild(wrap);
  }
  await reload();
}

// The media preview endpoint is auth-guarded, but an <img src> cannot send the
// admin Bearer token — so fetch the bytes WITH the token and hand the <img> a
// blob object URL instead (revoked on load to avoid leaking URLs).
async function loadAuthImage(img, path) {
  try {
    const res = await fetch(`/admin${path}`, {
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
    });
    if (!res.ok) { img.alt = "preview unavailable"; return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    img.src = url;
    img.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
  } catch (_) { img.alt = "preview unavailable"; }
}

function photoCard(ph, wr, reload, st) {
  const card = el("div", "npadmin-productcard");
  const img = el("img"); img.alt = "loading…";
  img.style.maxWidth = "160px"; img.style.maxHeight = "160px"; img.style.borderRadius = "8px";
  img.style.opacity = ph.active ? "1" : "0.4";
  loadAuthImage(img, `/retention/photos/${ph.id}/file`);
  card.appendChild(img);
  card.appendChild(el("div", "npadmin-meta",
    `#${ph.id} · stage ${ph.stage} · lvl ${ph.level_min} · views ${ph.views_count}` +
    (ph.active ? "" : " · inactive")));
  const desc = el("textarea", "npadmin-input"); desc.rows = 2; desc.value = ph.description || ""; desc.disabled = !wr;
  const tags = el("input", "npadmin-input"); tags.value = (ph.tags || []).join(", "); tags.disabled = !wr;
  const lvl = el("input", "npadmin-input"); lvl.type = "number"; lvl.value = ph.level_min; lvl.disabled = !wr;
  const stg = el("input", "npadmin-input"); stg.type = "number"; stg.value = ph.stage; stg.disabled = !wr;
  card.append(desc, tags, lvl, stg);
  if (wr) {
    const row = el("div", "npadmin-formrow");
    const save = el("button", "npadmin-btn ghost", "Save");
    save.addEventListener("click", async () => {
      try {
        await api(`/retention/photos/${ph.id}`, { method: "PUT", body: {
          description: desc.value, tags: tags.value.split(",").map((s) => s.trim()).filter(Boolean),
          level_min: Number(lvl.value), stage: Number(stg.value),
        } });
        st.style.color = "var(--good)"; st.textContent = "Saved";
      } catch (e) { st.textContent = e.message; }
    });
    const toggle = el("button", "npadmin-btn ghost", ph.active ? "Deactivate" : "Activate");
    toggle.addEventListener("click", async () => {
      try { await api(`/retention/photos/${ph.id}`, { method: "PUT", body: { active: !ph.active } }); reload(); }
      catch (e) { st.textContent = e.message; }
    });
    const del = el("button", "npadmin-btn ghost", "Delete");
    del.addEventListener("click", async () => {
      if (!confirm("Delete (deactivate) this photo?")) return;
      try { await api(`/retention/photos/${ph.id}`, { method: "DELETE" }); reload(); }
      catch (e) { st.textContent = e.message; }
    });
    row.append(save, toggle, del); card.appendChild(row);
  }
  return card;
}

// --- Managers pool ---------------------------------------------------------
async function retManagers(main, pid) {
  const st = el("div", "npadmin-err"); main.appendChild(st);
  const wr = canWrite();
  const listWrap = el("div");
  if (wr) {
    const add = el("div", "npadmin-productcard");
    add.appendChild(el("div", "npadmin-meta", "Add manager"));
    const name = el("input", "npadmin-input"); name.placeholder = "Display name (e.g. Masha NikaBet)";
    const user = el("input", "npadmin-input"); user.placeholder = "Telegram @username";
    const btn = el("button", "npadmin-btn", "Add");
    btn.addEventListener("click", async () => {
      if (!name.value.trim() || !user.value.trim()) { st.textContent = "Name and username required"; return; }
      try {
        await api("/retention/managers", { method: "POST", body: {
          product_id: pid, display_name: name.value.trim(), username: user.value.trim() } });
        name.value = user.value = ""; reload();
      } catch (e) { st.textContent = e.message; }
    });
    add.append(name, user, btn); main.appendChild(add);
  }
  main.appendChild(listWrap);
  async function reload() {
    listWrap.innerHTML = "";
    let items;
    try { items = (await api(`/retention/managers?product_id=${pid}`)).items; }
    catch (e) { listWrap.appendChild(errBox(e)); return; }
    const t = table(["Name", "Username", "Assigned", "Active", ""]);
    for (const m of items) {
      const tr = rowEls(t, [m.display_name, "@" + m.username, m.assigned_count,
        m.active ? "yes" : "no", ""]);
      if (wr) {
        const cell = tr.lastChild;
        const toggle = el("button", "npadmin-btn ghost", m.active ? "Disable" : "Enable");
        toggle.addEventListener("click", async () => {
          try { await api(`/retention/managers/${m.id}`, { method: "PUT", body: { active: !m.active } }); reload(); }
          catch (e) { st.textContent = e.message; }
        });
        const del = el("button", "npadmin-btn ghost", "Delete");
        del.addEventListener("click", async () => {
          if (!confirm("Delete this manager?")) return;
          try { await api(`/retention/managers/${m.id}`, { method: "DELETE" }); reload(); }
          catch (e) { st.textContent = e.message; }
        });
        cell.append(toggle, del);
      }
    }
    listWrap.appendChild(t);
  }
  await reload();
}

// --- Retention settings group (JSON editor over /admin/settings/retention) --
async function retConfig(main, pid) {
  const st = el("div", "npadmin-err"); main.appendChild(st);
  let data;
  try { data = await api(`/settings?product_id=${pid}`); }
  catch (e) { main.appendChild(errBox(e)); return; }
  const resolved = (data.resolved || {}).retention || {};
  const override = (data.overrides || {}).retention || {};
  main.appendChild(el("div", "npadmin-meta",
    "Retention knobs (daily_photo_cap, proactive_photo_cooldown_msgs, " +
    "candidate_list_size, stage_advance_msgs, stage_advance_min_hours, max_stage, " +
    "max_stage_by_tier, vip_tiers, nonce_ttl_sec). Edit the JSON; only changed " +
    "keys need to be present. Resolved (effective) values shown."));
  const ta = el("textarea", "npadmin-input"); ta.rows = 16;
  ta.value = JSON.stringify(Object.keys(override).length ? override : resolved, null, 2);
  ta.disabled = !canWrite();
  main.appendChild(ta);
  main.appendChild(el("div", "npadmin-meta",
    "Effective now: " + JSON.stringify(resolved)));
  if (canWrite()) {
    const btn = el("button", "npadmin-btn", "Save retention settings");
    btn.addEventListener("click", async () => {
      let parsed;
      try { parsed = JSON.parse(ta.value); }
      catch (e) { st.textContent = "Invalid JSON: " + e.message; return; }
      st.textContent = ""; st.style.color = "";
      try {
        await api(`/settings/retention?product_id=${pid}`, { method: "PUT", body: { value: parsed } });
        st.style.color = "var(--good)"; st.textContent = "Saved";
      } catch (e) { st.textContent = e.message; }
    });
    main.appendChild(btn);
  }
}

// --- Analytics -------------------------------------------------------------
async function retAnalytics(main, pid) {
  main.appendChild(dateToolbar(() => routeView(document.getElementById("npadmin-main"))));
  const cards = el("div", "npadmin-cards"); main.appendChild(cards);
  const usersWrap = el("div"); main.appendChild(usersWrap);
  cards.appendChild(el("div", "npadmin-meta", "Loading…"));
  try {
    const o = await api(`/retention/overview?product_id=${pid}${dateQS()}`);
    cards.innerHTML = "";
    card(cards, o.users_total, "Users total");
    card(cards, o.users_subscribed, "Subscribed");
    card(cards, o.users_active, "Active in range");
    card(cards, o.avg_stage, "Avg stage");
    card(cards, o.photos_sent, "Photos sent");
    card(cards, o.handoffs, "Handoffs");
  } catch (e) { cards.innerHTML = ""; cards.appendChild(errBox(e)); }
  try {
    const { items } = await api(`/retention/users?product_id=${pid}`);
    const t = table(["tg id", "username", "player", "entry", "VIP", "stage",
      "sub", "msgs", "photos", "manager", "last active"]);
    for (const u of items) rowEls(t, [u.tg_user_id, u.tg_username || "—",
      u.player_id || "—", u.entry_type, u.vip_level || "—", u.unlocked_stage,
      u.subscribed ? "yes" : "no", u.meaningful_msgs, u.photos_total,
      u.manager_name || "—", fmtDateTime(u.last_active_at)]);
    usersWrap.appendChild(el("h3", null, "Users"));
    const scroll = el("div", "npadmin-table-scroll"); scroll.appendChild(t);
    usersWrap.appendChild(scroll);
  } catch (e) { usersWrap.appendChild(errBox(e)); }
}

// Date query fragment (?from=&to=) for retention analytics — leading '&' safe
// because callers append it after ?product_id=.
function dateQS() { return `&from=${state.from}&to=${state.to}`; }

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
// Sync view + main content when the user navigates with browser back/forward.
window.addEventListener("popstate", () => {
  if (!state.token) return;
  const { view, param } = parseHash();
  const mainEl = document.getElementById("npadmin-main");
  if (!mainEl) return;
  if (view === "sessions" && param) {
    openSession(param);   // sets state.view + syncs the nav itself
  } else {
    state.view = _VALID_VIEWS.includes(view) ? view : "overview";
    state.param = _VALID_VIEWS.includes(view) ? param : null;
    syncNavActive();
    routeView(mainEl);
  }
});

if (state.token) renderApp(); else renderLogin();
