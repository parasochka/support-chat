// NowPlix support-chat floating widget — vanilla ES module, no build step.
//
// Embed contract (for the host site later): one script tag + a config object.
//   <script type="module" src="/widget.js"></script>
// or import and call mount() with overrides:
//   import { mount } from "/widget.js";
//   mount({ apiBase: "https://chat.example.com", userContext: {...} });
//
// Owner tuning block — tweak without digging into the logic below.
export const CONFIG = {
  // Base URL of this service. "" = same origin as the page that loads the widget.
  API_BASE: "",
  // reCaptcha v3 site key. Leave "" to skip captcha (dev).
  RECAPTCHA_SITE_KEY: "6LfNeistAAAAADIKPj_VP-AcInrFei0FLqabNK8X",
  // Sample user_context for the test build. In production the host page supplies this.
  USER_CONTEXT: {
    id: "demo-12345",
    full_name: "Test Player",
    email: "test.player@example.com",
    activation_status: "active",
  },
  // Optional explicit answer language ("en","es","ru","tr","pt") or null to auto-detect.
  LANG: null,
  // Browser language — used to localise the UI and as the default answer
  // language. navigator.languages[0] is the user's top preference; fall back to
  // navigator.language. The player can still write in any language and the AI
  // mirrors it; this only seeds the default.
  LOCALE:
    (typeof navigator !== "undefined" &&
      ((navigator.languages && navigator.languages[0]) || navigator.language)) ||
    null,
};

// UI string translations. The chat *answers* follow the player's language
// (handled server-side); these only cover the widget chrome so a Russian
// browser doesn't see an English shell.
const I18N = {
  en: { support: "Support", topics: "What can we help you with?", other: "Other",
        greeting: "Hi! How can I help you today?", placeholder: "Type your message…",
        send: "Send", launcher: "Open support chat",
        startError: "Could not start chat. Please try again later.",
        sendError: "Something went wrong. Please try again." },
  ru: { support: "Поддержка", topics: "Чем мы можем помочь?", other: "Другое",
        greeting: "Здравствуйте! Чем можем помочь?", placeholder: "Введите сообщение…",
        send: "Отправить", launcher: "Открыть чат поддержки",
        startError: "Не удалось начать чат. Попробуйте позже.",
        sendError: "Что-то пошло не так. Попробуйте ещё раз." },
  es: { support: "Soporte", topics: "¿En qué podemos ayudarte?", other: "Otro",
        greeting: "¡Hola! ¿En qué podemos ayudarte hoy?", placeholder: "Escribe tu mensaje…",
        send: "Enviar", launcher: "Abrir chat de soporte",
        startError: "No se pudo iniciar el chat. Inténtalo más tarde.",
        sendError: "Algo salió mal. Inténtalo de nuevo." },
  tr: { support: "Destek", topics: "Size nasıl yardımcı olabiliriz?", other: "Diğer",
        greeting: "Merhaba! Bugün size nasıl yardımcı olabiliriz?",
        placeholder: "Mesajınızı yazın…", send: "Gönder", launcher: "Destek sohbetini aç",
        startError: "Sohbet başlatılamadı. Lütfen daha sonra tekrar deneyin.",
        sendError: "Bir şeyler ters gitti. Lütfen tekrar deneyin." },
  pt: { support: "Suporte", topics: "Como podemos ajudar?", other: "Outro",
        greeting: "Olá! Como podemos ajudar hoje?", placeholder: "Digite sua mensagem…",
        send: "Enviar", launcher: "Abrir chat de suporte",
        startError: "Não foi possível iniciar o chat. Tente novamente mais tarde.",
        sendError: "Algo deu errado. Tente novamente." },
};

function baseLang(code) {
  if (!code) return null;
  const base = String(code).replace("_", "-").split("-")[0].toLowerCase();
  return I18N[base] ? base : null;
}

// Best initial guess before the backend confirms: explicit LANG, else browser.
function initialLang() {
  return baseLang(CONFIG.LANG) || baseLang(CONFIG.LOCALE) || "en";
}

function t(key) {
  const dict = I18N[state.lang] || I18N.en;
  return dict[key] || I18N.en[key] || key;
}

const state = {
  sessionId: null,
  token: null,
  topics: [],
  lang: initialLang(),
  topicChosen: false,
  open: false,
};

// ---------------------------------------------------------------------------
// reCaptcha helper (no-op when no site key configured)
// ---------------------------------------------------------------------------
function loadRecaptcha(siteKey) {
  return new Promise((resolve) => {
    if (!siteKey) return resolve(null);
    if (window.grecaptcha) return resolve(window.grecaptcha);
    const s = document.createElement("script");
    s.src = `https://www.google.com/recaptcha/api.js?render=${siteKey}`;
    s.onload = () => resolve(window.grecaptcha);
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
  });
}

async function recaptchaToken(action) {
  if (!CONFIG.RECAPTCHA_SITE_KEY) return null;
  const grc = await loadRecaptcha(CONFIG.RECAPTCHA_SITE_KEY);
  if (!grc) return null;
  return new Promise((resolve) => {
    grc.ready(() => {
      grc.execute(CONFIG.RECAPTCHA_SITE_KEY, { action })
        .then(resolve)
        .catch(() => resolve(null));
    });
  });
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------
async function api(path, { method = "POST", body = null, auth = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const res = await fetch(`${CONFIG.API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { data = {}; }
  return { ok: res.ok, status: res.status, data };
}

async function createSession() {
  const token = await recaptchaToken("chat_session");
  const { ok, data } = await api("/api/chat/session", {
    body: {
      consumer: "web-test",
      player_id: CONFIG.USER_CONTEXT.id || null,
      user_context: CONFIG.USER_CONTEXT,
      lang: CONFIG.LANG,
      locale: CONFIG.LOCALE,
      recaptcha_token: token,
    },
  });
  if (!ok) throw new Error("session create failed");
  state.sessionId = data.session_id;
  state.token = data.token;
  state.topics = data.topics || [];
  // Backend resolves the default language (browser locale wins over the server
  // default); refresh the UI chrome to match it.
  state.lang = baseLang(data.lang) || state.lang;
  applyStaticLabels();
}

async function selectTopic(slug) {
  await api("/api/chat/topic", {
    auth: true,
    body: { session_id: state.sessionId, topic_slug: slug },
  });
  state.topicChosen = true;
}

async function sendMessage(text) {
  const { ok, data, status } = await api("/api/chat/message", {
    auth: true,
    body: { session_id: state.sessionId, text },
  });
  if (!ok) {
    return { reply: data.detail || `Error (${status})`, escalation: { active: false } };
  }
  return data;
}

// ---------------------------------------------------------------------------
// DOM rendering (all class names prefixed npchat- to avoid host collisions)
// ---------------------------------------------------------------------------
let els = {};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function buildUI() {
  const root = el("div", "npchat-root");

  const launcher = el("button", "npchat-launcher");
  launcher.setAttribute("aria-label", t("launcher"));
  launcher.innerHTML = "&#128172;";
  launcher.addEventListener("click", togglePanel);

  const panel = el("div", "npchat-panel npchat-hidden");

  const header = el("div", "npchat-header");
  header.appendChild(el("span", "npchat-title", t("support")));
  const closeBtn = el("button", "npchat-close", "✕");
  closeBtn.addEventListener("click", togglePanel);
  header.appendChild(closeBtn);

  const body = el("div", "npchat-body");
  const topics = el("div", "npchat-topics");
  const messages = el("div", "npchat-messages npchat-hidden");

  const inputRow = el("div", "npchat-inputrow npchat-hidden");
  const input = el("input", "npchat-input");
  input.type = "text";
  input.placeholder = t("placeholder");
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") onSend();
  });
  const sendBtn = el("button", "npchat-send", t("send"));
  sendBtn.addEventListener("click", onSend);
  inputRow.appendChild(input);
  inputRow.appendChild(sendBtn);

  body.appendChild(topics);
  body.appendChild(messages);

  panel.appendChild(header);
  panel.appendChild(body);
  panel.appendChild(inputRow);

  root.appendChild(panel);
  root.appendChild(launcher);
  document.body.appendChild(root);

  els = { root, launcher, panel, body, topics, messages, inputRow, input, sendBtn };
}

// Re-apply chrome strings after the resolved language is known (post-session).
function applyStaticLabels() {
  if (!els.root) return;
  els.launcher.setAttribute("aria-label", t("launcher"));
  const title = els.panel.querySelector(".npchat-title");
  if (title) title.textContent = t("support");
  els.input.placeholder = t("placeholder");
  els.sendBtn.textContent = t("send");
}

function renderTopics() {
  els.topics.innerHTML = "";
  const heading = el("div", "npchat-topics-h", t("topics"));
  els.topics.appendChild(heading);
  for (const topic of state.topics) {
    const b = el("button", "npchat-topic", topic.title);
    b.addEventListener("click", () => onTopic(topic.slug));
    els.topics.appendChild(b);
  }
  const other = el("button", "npchat-topic npchat-topic-other", t("other"));
  other.addEventListener("click", () => onTopic("other"));
  els.topics.appendChild(other);
}

function addMessage(role, text) {
  const m = el("div", `npchat-msg npchat-msg-${role}`, text);
  els.messages.appendChild(m);
  els.messages.scrollTop = els.messages.scrollHeight;
  return m;
}

function addEscalation(esc) {
  const wrap = el("div", "npchat-escalation");
  if (esc.message) wrap.appendChild(el("div", "npchat-esc-msg", esc.message));
  if (esc.button && esc.button.url) {
    const a = el("a", "npchat-esc-btn", esc.button.label || "Contact support");
    a.href = esc.button.url;
    a.target = "_blank";
    a.rel = "noopener";
    wrap.appendChild(a);
  } else if (esc.button) {
    wrap.appendChild(el("div", "npchat-esc-note",
      "(Contact form URL not configured — set CONTACT_FORM_URL)"));
  }
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ---------------------------------------------------------------------------
// flow handlers
// ---------------------------------------------------------------------------
async function togglePanel() {
  state.open = !state.open;
  els.panel.classList.toggle("npchat-hidden", !state.open);
  if (state.open && !state.sessionId) {
    try {
      await createSession();
      renderTopics();
    } catch (e) {
      els.topics.innerHTML = "";
      addMessageToTopics(t("startError"));
    }
  }
}

function addMessageToTopics(text) {
  els.topics.appendChild(el("div", "npchat-error", text));
}

async function onTopic(slug) {
  try {
    await selectTopic(slug);
  } catch (_) { /* non-fatal: still allow chatting */ }
  els.topics.classList.add("npchat-hidden");
  els.messages.classList.remove("npchat-hidden");
  els.inputRow.classList.remove("npchat-hidden");
  addMessage("assistant", t("greeting"));
  els.input.focus();
}

async function onSend() {
  const text = els.input.value.trim();
  if (!text) return;
  els.input.value = "";
  addMessage("user", text);
  const typing = addMessage("assistant", "…");
  try {
    const data = await sendMessage(text);
    typing.textContent = data.reply || "";
    if (data.escalation && data.escalation.active) {
      addEscalation(data.escalation);
    }
  } catch (e) {
    typing.textContent = t("sendError");
  }
}

// ---------------------------------------------------------------------------
// public mount()
// ---------------------------------------------------------------------------
export function mount(overrides = {}) {
  Object.assign(CONFIG, overrides);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildUI);
  } else {
    buildUI();
  }
}

// Auto-mount when loaded directly as a module (test build convenience).
if (typeof window !== "undefined" && !window.__NPCHAT_NO_AUTOMOUNT__) {
  mount();
}
