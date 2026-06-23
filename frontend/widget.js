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
  // `language` is the account/profile language. It is the strongest signal for the
  // widget chrome (after an explicit LANG), so a Russian account on an English
  // browser opens in Russian — and it does so on the FIRST paint, before any
  // network round-trip, so the chrome never flips after open.
  USER_CONTEXT: {
    id: "demo-12345",
    full_name: "Test Player",
    email: "test.player@example.com",
    activation_status: "active",
    language: null,
  },
  // Signed handshake blob from the host backend (HMAC over user_context+exp).
  // When the service has WIDGET_HANDSHAKE_SECRET set, this is the ONLY trusted
  // source of user_context; the raw USER_CONTEXT above is ignored server-side.
  // Leave null for anonymous/dev sessions.
  SIGNED_CONTEXT: null,
  // Browser language — the single source for both the UI and the answer
  // language. navigator.languages[0] is the user's top preference; fall back to
  // navigator.language.
  LOCALE:
    (typeof navigator !== "undefined" &&
      ((navigator.languages && navigator.languages[0]) || navigator.language)) ||
    null,
};

// UI string translations. The chat *answers* are in the same (browser) language
// (handled server-side); these only cover the widget chrome so a Russian
// browser doesn't see an English shell.
const I18N = {
  en: { support: "Support", topics: "What can we help you with?", other: "Other",
        back: "Back to topics",
        greeting: "Hi! How can I help you today?", placeholder: "Type your message…",
        send: "Send", launcher: "Open support chat",
        startError: "Could not start chat. Please try again later.",
        sendError: "Something went wrong. Please try again.",
        suggest: "It looks like your question is about “{topic}”.",
        switchTopic: "Switch to “{topic}”",
        switching: "Looks like this is about “{topic}” — switching you there…",
        finish: "End chat", finished: "Chat ended. Thanks for reaching out!" },
  ru: { support: "Поддержка", topics: "Чем мы можем помочь?", other: "Другое",
        back: "К выбору темы",
        greeting: "Здравствуйте! Чем можем помочь?", placeholder: "Введите сообщение…",
        send: "Отправить", launcher: "Открыть чат поддержки",
        startError: "Не удалось начать чат. Попробуйте позже.",
        sendError: "Что-то пошло не так. Попробуйте ещё раз.",
        suggest: "Похоже, ваш вопрос относится к теме «{topic}».",
        switchTopic: "Перейти в «{topic}»",
        switching: "Похоже, это про «{topic}» — перевожу вас туда…",
        finish: "Завершить чат", finished: "Чат завершён. Спасибо за обращение!" },
  es: { support: "Soporte", topics: "¿En qué podemos ayudarte?", other: "Otro",
        back: "Volver a los temas",
        greeting: "¡Hola! ¿En qué podemos ayudarte hoy?", placeholder: "Escribe tu mensaje…",
        send: "Enviar", launcher: "Abrir chat de soporte",
        startError: "No se pudo iniciar el chat. Inténtalo más tarde.",
        sendError: "Algo salió mal. Inténtalo de nuevo.",
        suggest: "Parece que tu pregunta es sobre «{topic}».",
        switchTopic: "Cambiar a «{topic}»",
        switching: "Parece que esto es sobre «{topic}» — te llevo allí…",
        finish: "Finalizar chat", finished: "Chat finalizado. ¡Gracias por contactarnos!" },
  tr: { support: "Destek", topics: "Size nasıl yardımcı olabiliriz?", other: "Diğer",
        back: "Konulara dön",
        greeting: "Merhaba! Bugün size nasıl yardımcı olabiliriz?",
        placeholder: "Mesajınızı yazın…", send: "Gönder", launcher: "Destek sohbetini aç",
        startError: "Sohbet başlatılamadı. Lütfen daha sonra tekrar deneyin.",
        sendError: "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
        suggest: "Sorunuz “{topic}” konusuyla ilgili görünüyor.",
        switchTopic: "“{topic}” konusuna geç",
        switching: "Bu “{topic}” ile ilgili görünüyor — sizi oraya alıyorum…",
        finish: "Sohbeti bitir", finished: "Sohbet sona erdi. Bize ulaştığınız için teşekkürler!" },
  pt: { support: "Suporte", topics: "Como podemos ajudar?", other: "Outro",
        back: "Voltar aos tópicos",
        greeting: "Olá! Como podemos ajudar hoje?", placeholder: "Digite sua mensagem…",
        send: "Enviar", launcher: "Abrir chat de suporte",
        startError: "Não foi possível iniciar o chat. Tente novamente mais tarde.",
        sendError: "Algo deu errado. Tente novamente.",
        suggest: "Parece que sua pergunta é sobre “{topic}”.",
        switchTopic: "Mudar para “{topic}”",
        switching: "Parece que isto é sobre “{topic}” — levando você até lá…",
        finish: "Encerrar chat", finished: "Chat encerrado. Obrigado pelo contato!" },
};

// Per-topic emoji so each menu item reads at a glance. Keyed by the backend
// topic slug; unknown slugs fall back to a neutral bubble so a newly-added
// topic still renders something. "other" is special-cased in
// renderTopics() with an AI-associated icon (it's the "ask anything" catch-all).
const TOPIC_EMOJI = {
  deposits: "💳",
  withdrawals: "💸",
  account_kyc: "🪪",
  bonuses: "🎁",
  betting_games: "🎲",
  technical: "🛠️",
  other: "🤖",
  _default: "💬",
};

function topicEmoji(slug) {
  return TOPIC_EMOJI[slug] || TOPIC_EMOJI._default;
}

function baseLang(code) {
  if (!code) return null;
  const base = String(code).replace("_", "-").split("-")[0].toLowerCase();
  return I18N[base] ? base : null;
}

// The widget's STARTING language, decided synchronously at load — before the
// panel is ever painted — from the browser language, falling back to English.
// This is the same language the backend resolves from the locale, so chrome and
// answers agree from turn one with no post-/session flicker. It can later follow
// the conversation if the player switches language mid-chat (see maybeSwitchLang).
function resolveLang() {
  return baseLang(CONFIG.LOCALE) || "en";
}

function t(key) {
  const dict = I18N[state.lang] || I18N.en;
  return dict[key] || I18N.en[key] || key;
}

// t() with a {topic} placeholder filled in (topic-switch suggestion copy).
function tTopic(key, topic) {
  return t(key).replace("{topic}", topic);
}

const state = {
  sessionId: null,
  token: null,
  topics: [],
  // True once the lightweight catalogue (GET /topics) has been fetched, so the
  // panel can paint the category buttons before the session exists.
  topicsLoaded: false,
  // In-flight promise for the (slow) reCaptcha + session create, started in the
  // background on open so it never blocks the first paint. ensureSession() awaits
  // it lazily before any action that actually needs a token.
  sessionPromise: null,
  // The widget chrome language. Starts at the browser locale and may later
  // follow the conversation if the player switches language (maybeSwitchLang).
  lang: resolveLang(),
  // The locale handed to the NEXT session create / topic fetch. Starts at the
  // browser locale and then FOLLOWS the conversation language (maybeSwitchLang),
  // so a fresh session minted after back / close / finish / escalation inherits
  // the language the player drifted to instead of snapping back to the browser
  // language. Without this the chrome (and the new session's base language)
  // flickered back to the browser locale every time the session was abandoned.
  locale: CONFIG.LOCALE,
  topicChosen: false,
  open: false,
  // True while the mobile full-screen sheet is active (geometry driven by JS).
  fullscreen: false,
  greetingEl: null,
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

// Fetch just the topic catalogue — no session, token, or reCaptcha — so the
// panel can paint the category buttons instantly on open. Cheap + cacheable;
// safe to call speculatively on mount. We pass the already-resolved chrome
// language so the titles come back in the right language on the first paint —
// the catalogue must follow `state.lang`, never redefine it.
async function fetchTopics() {
  const qs = new URLSearchParams({ lang: state.lang });
  const { ok, data } = await api(`/api/chat/topics?${qs.toString()}`,
                                 { method: "GET" });
  if (!ok) throw new Error("topics fetch failed");
  state.topics = data.topics || [];
  state.topicsLoaded = true;
  applyStaticLabels();
}

// Kick off (once) and await the background session create. Anything that needs
// a valid token — picking a topic, sending — funnels through here so it
// transparently waits for the in-flight session instead of starting its own.
// Errors propagate so callers can surface a "couldn't start" message.
function ensureSession() {
  if (state.sessionId) return Promise.resolve();
  if (!state.sessionPromise) {
    // Don't cache a REJECTED promise: a transient createSession failure (network
    // blip, a session-create rate-limit 429, a reCaptcha hiccup) must not wedge
    // every later attempt forever — clear it on failure so the next call retries
    // cleanly instead of replaying the same rejection ("nothing happens").
    state.sessionPromise = createSession().catch((e) => {
      state.sessionPromise = null;
      throw e;
    });
  }
  return state.sessionPromise;
}

function resetSessionState() {
  state.sessionId = null;
  state.token = null;
  state.sessionPromise = null;
  state.topicChosen = false;
  state.greetingEl = null;
}

// Return the panel to a clean topic picker. With `abandon: true` it also drops
// the current session so the next engagement creates a BRAND-NEW one — the
// single seam behind "every open / back / re-entry is a fresh conversation".
// The old conversation is left as an abandoned `open` session server-side (the
// Unresolved queue already accounts for those); we never resume it client-side.
function resetToPicker({ abandon } = {}) {
  if (abandon) resetSessionState();
  if (!els.root) return;
  els.back.classList.add("npchat-hidden");
  els.inputRow.classList.add("npchat-hidden");
  clearSuggestions();
  els.messages.classList.add("npchat-hidden");
  els.messages.innerHTML = "";
  state.greetingEl = null;
  state.topicChosen = false;
  els.topics.classList.remove("npchat-hidden");
}

async function createSession() {
  const token = await recaptchaToken("chat_session");
  const { ok, data } = await api("/api/chat/session", {
    body: {
      consumer: "web-test",
      player_id: CONFIG.USER_CONTEXT.id || null,
      user_context: CONFIG.USER_CONTEXT,
      signed_context: CONFIG.SIGNED_CONTEXT,
      // The widget's current language: the browser locale on the first session,
      // then whatever the conversation drifted to (state.locale follows
      // maybeSwitchLang). The backend resolves the same answer/chrome language
      // from it, so the two agree from turn one — and a session minted after
      // back / close / finish stays in the drifted language instead of snapping
      // back to the browser locale.
      locale: state.locale,
      recaptcha_token: token,
    },
  });
  if (!ok) throw new Error("session create failed");
  state.sessionId = data.session_id;
  state.token = data.token;
  state.topics = data.topics || [];
  state.topicsLoaded = true;
  // The chrome language was resolved up front from the browser; this (slow)
  // response only needs to (re)paint the topic list. It can still follow a later
  // conversation-language switch (maybeSwitchLang), just not from /session.
  applyStaticLabels();
  if (els.topics && !els.topics.classList.contains("npchat-hidden")) {
    renderTopics();
  }
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
    // The session was already closed (resolved) or handed off (escalated): the
    // backend rejects further turns with 409. Don't surface a raw error — end the
    // conversation locally so the next open starts a fresh session.
    if (status === 409 || (data && data.error === "session_closed")) {
      endConversation();
      return { reply: t("finished"), escalation: { active: false } };
    }
    return { reply: data.detail || `Error (${status})`, escalation: { active: false } };
  }
  // The conversation language can drift when the player starts writing in
  // another supported language; follow it in the chrome too (see maybeSwitchLang).
  maybeSwitchLang(data);
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

  const headerLeft = el("div", "npchat-header-left");
  // Back arrow — only shown inside a conversation, lets the player return to the
  // topic picker to choose a different topic. Hidden on the topic list itself.
  const backBtn = el("button", "npchat-back npchat-hidden");
  backBtn.setAttribute("aria-label", t("back"));
  backBtn.innerHTML =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
    'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M15 18l-6-6 6-6"/></svg>';
  backBtn.addEventListener("click", goBackToTopics);
  headerLeft.appendChild(backBtn);
  headerLeft.appendChild(el("span", "npchat-title", t("support")));
  header.appendChild(headerLeft);

  const headerRight = el("div", "npchat-header-right");
  const closeBtn = el("button", "npchat-close", "✕");
  closeBtn.addEventListener("click", togglePanel);
  headerRight.appendChild(closeBtn);
  header.appendChild(headerRight);

  const body = el("div", "npchat-body");
  const topics = el("div", "npchat-topics");
  const messages = el("div", "npchat-messages npchat-hidden");

  // One-tap "guide-to-KB" question bubbles (+ the resolved "finish chat" button)
  // sit just above the input field. Populated per assistant turn from the
  // response's `suggestions` / `resolved`; hidden whenever it's empty.
  const suggestions = el("div", "npchat-suggestions npchat-hidden");

  const inputRow = el("div", "npchat-inputrow npchat-hidden");
  const input = el("input", "npchat-input");
  input.type = "text";
  input.placeholder = t("placeholder");
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") onSend();
  });
  // The on-screen keyboard animates in over ~300ms and some browsers fire the
  // VisualViewport resize late (or not at all) on focus — re-pin the sheet a
  // few times so the input row never ends up hidden behind the keyboard.
  input.addEventListener("focus", () => {
    if (!state.fullscreen) return;
    [0, 150, 350, 600].forEach((d) => setTimeout(syncFullscreenGeometry, d));
  });
  input.addEventListener("blur", () => {
    if (!state.fullscreen) return;
    [0, 150, 350].forEach((d) => setTimeout(syncFullscreenGeometry, d));
  });
  const sendBtn = el("button", "npchat-send", t("send"));
  sendBtn.addEventListener("click", onSend);
  inputRow.appendChild(input);
  inputRow.appendChild(sendBtn);

  body.appendChild(topics);
  body.appendChild(messages);

  panel.appendChild(header);
  panel.appendChild(body);
  panel.appendChild(suggestions);
  panel.appendChild(inputRow);

  root.appendChild(panel);
  root.appendChild(launcher);
  document.body.appendChild(root);

  els = { root, launcher, panel, body, topics, messages, suggestions, inputRow,
          input, sendBtn, back: backBtn };

  // Keep the open panel in the right mode if the viewport class changes
  // (rotate, window resize, responsive devtools): enter the full-screen sheet
  // when we cross into mobile, leave it when we cross back to desktop.
  const reclassify = () => {
    if (!state.open) return;
    const mobile = isMobileViewport();
    if (mobile && !state.fullscreen) {
      enterFullscreen();
      setBodyScrollLock(true);
    } else if (!mobile && state.fullscreen) {
      exitFullscreen();
      setBodyScrollLock(false);
    }
  };
  window.addEventListener("resize", reclassify);
  window.addEventListener("orientationchange", reclassify);

  // Speculatively warm the topic catalogue so the very first open paints the
  // category buttons instantly. It's a cheap, cacheable, session-free GET and
  // touches no reCaptcha or DB, so doing it on mount is cheap insurance.
  fetchTopics().catch(() => { /* the open handler retries if this missed */ });
}

// Re-apply chrome strings. Idempotent, and re-run whenever the language changes
// (initial paint is the browser language; later it may follow the conversation).
function applyStaticLabels() {
  if (!els.root) return;
  els.launcher.setAttribute("aria-label", t("launcher"));
  const title = els.panel.querySelector(".npchat-title");
  if (title) title.textContent = t("support");
  if (els.back) els.back.setAttribute("aria-label", t("back"));
  els.input.placeholder = t("placeholder");
  els.sendBtn.textContent = t("send");
}

// Follow a conversation-language drift in the CHROME. The chrome opens in the
// browser language, but if the player starts writing in another supported
// language the answers switch server-side (response `lang`); mirror that here so
// the whole widget — shell + answers — moves together. A no-op when the language
// is unchanged or unsupported, so normal same-language turns cost nothing.
function maybeSwitchLang(data) {
  const next = data && baseLang(data.lang);
  if (!next || next === state.lang) return;
  state.lang = next;
  // Carry the drift into the locale used for the NEXT session create / topic
  // fetch too, so abandoning this session (back / close / finish / escalation)
  // and minting a fresh one keeps the widget in the drifted language instead of
  // snapping back to the browser locale (the "language flickers to English on
  // back" bug).
  state.locale = next;
  applyStaticLabels();
  // Re-localize the canned greeting — the only chrome-language bubble in the
  // transcript (real answers are already in the new language).
  if (state.greetingEl) state.greetingEl.textContent = t("greeting");
  // Refresh topic titles in the new language (cheap cached GET) and re-render
  // the picker if it's currently visible.
  fetchTopics()
    .then(() => {
      if (els.topics && !els.topics.classList.contains("npchat-hidden")) {
        renderTopics();
      }
    })
    .catch(() => { /* non-fatal: titles refresh on next open */ });
}

function renderTopics() {
  els.topics.innerHTML = "";
  const heading = el("div", "npchat-topics-h", t("topics"));
  els.topics.appendChild(heading);
  for (const topic of state.topics) {
    els.topics.appendChild(topicButton(topic.slug, topic.title));
  }
  // "Other" is the always-available escape hatch: if the player didn't find
  // their topic they can still ask anything here, so make it visually distinct
  // (purple outline + AI icon) and never let it blend into the list above.
  els.topics.appendChild(topicButton("other", t("other"), "npchat-topic-other"));
}

// Build a topic button as an emoji badge + label so the icon and text align
// regardless of title length. `extraCls` adds modifier classes (e.g. "other").
function topicButton(slug, title, extraCls) {
  const cls = "npchat-topic" + (extraCls ? " " + extraCls : "");
  const b = el("button", cls);
  b.appendChild(el("span", "npchat-topic-emoji", topicEmoji(slug)));
  b.appendChild(el("span", "npchat-topic-label", title));
  b.addEventListener("click", () => onTopic(slug));
  return b;
}

// ---------------------------------------------------------------------------
// Minimal, SAFE Markdown rendering for assistant replies
// ---------------------------------------------------------------------------
// The model formats answers with light Markdown on its own — **bold**, numbered
// and bulleted lists, the odd `code` span or link. Rendered as plain text those
// markers leak to the screen (the player sees literal "**Бонус**" with the
// asterisks). This renders a small whitelisted subset to HTML so the formatting
// shows the way the model meant it, and no stray markup reaches the user.
//
// Security: the result is injected as innerHTML, so the model's text is fully
// HTML-escaped FIRST and only a fixed set of inline/block markup is then
// re-introduced from trusted patterns — no raw HTML from the model survives.
// Links are restricted to http(s)/mailto and forced to open with rel="noopener".
// Only assistant turns go through here; user input is always rendered literally.
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Inline spans on already-escaped text. `code` spans and links are pulled out
// to placeholders FIRST so their literal contents (e.g. a URL's underscores, or
// the generated target="_blank") can't be re-chewed by the bold/italic rules;
// bold runs before italic so the single-asterisk rule never eats a `**` pair;
// then the protected tokens are restored.
function renderInline(text) {
  const tokens = [];
  // Stash code spans / links behind private-use sentinels so their literal
  // contents (a URL's underscores, the generated target="_blank") can't be
  // re-chewed by the bold/italic rules below; restored at the very end. The
  // \uE000/\uE001 sentinels can't occur in the already-escaped input text.
  const stash = (html) => "\uE000" + (tokens.push(html) - 1) + "\uE001";
  return text
    .replace(/`([^`]+)`/g, (_m, c) => stash(`<code>${c}</code>`))
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g,
      (_m, label, url) =>
        stash(`<a href="${url}" target="_blank" rel="noopener">${label}</a>`),
    )
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*/g, "$1<em>$2</em>")
    .replace(/(^|[^\w_])_(?!\s)([^_\n]+?)_/g, "$1<em>$2</em>")
    .replace(/\uE000(\d+)\uE001/g, (_m, i) => tokens[Number(i)]);
}

// Block structure: groups consecutive list items into <ul>/<ol>, turns ATX
// headings and blank-line-separated runs into paragraphs, and keeps single line
// breaks inside a paragraph as <br>. Returns HTML with no significant inter-tag
// whitespace (assistant bubbles drop pre-wrap so this markup drives the layout).
function renderMarkdown(md) {
  const lines = escapeHtml(md == null ? "" : md).split(/\r?\n/);
  const out = [];
  let list = null;
  let para = [];
  const flushList = () => {
    if (!list) return;
    out.push(
      `<${list.type}>` +
        list.items.map((it) => `<li>${renderInline(it)}</li>`).join("") +
        `</${list.type}>`,
    );
    list = null;
  };
  const flushPara = () => {
    if (!para.length) return;
    out.push(`<p>${para.map(renderInline).join("<br>")}</p>`);
    para = [];
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const ul = line.match(/^\s*[-*•]\s+(.*)$/);
    if (heading) {
      flushList();
      flushPara();
      out.push(`<p><strong>${renderInline(heading[1])}</strong></p>`);
    } else if (ol) {
      flushPara();
      if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; }
      list.items.push(ol[1]);
    } else if (ul) {
      flushPara();
      if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; }
      list.items.push(ul[1]);
    } else if (!line.trim()) {
      flushList();
      flushPara();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushList();
  flushPara();
  return out.join("");
}

// Fill a message bubble: assistant turns get the rendered Markdown subset, every
// other role stays literal text (user input is never treated as markup).
function setMsgBody(elm, role, text) {
  if (role === "assistant") elm.innerHTML = renderMarkdown(text);
  else elm.textContent = text == null ? "" : text;
}

// Pin the transcript to its newest content. The scrolling element is
// `.npchat-body` (it owns `overflow-y: auto`), NOT `.npchat-messages` — so this
// is what keeps the latest turn in view without the player scrolling by hand.
function scrollToBottom() {
  if (!els.body) return;
  els.body.scrollTop = els.body.scrollHeight;
  // Re-pin after the next layout pass. The suggestion strip lives OUTSIDE the
  // scroll container (it's a sibling of .npchat-body), so showing it a moment
  // later shrinks the transcript's viewport — and rendered Markdown / fonts can
  // also reflow a frame after the text lands. Without this second pin the newest
  // turn ends up clipped above the fold ("the new message is half-hidden behind
  // the follow-up bubbles").
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      if (els.body) els.body.scrollTop = els.body.scrollHeight;
    });
  }
}

function addMessage(role, text) {
  const m = el("div", `npchat-msg npchat-msg-${role}`);
  setMsgBody(m, role, text);
  els.messages.appendChild(m);
  scrollToBottom();
  return m;
}

// A "typing" placeholder bubble — three bouncing dots shown while the model is
// generating the reply, so the player sees the request is in flight and an
// answer is coming. Replaced in place by fillTyping() once the turn resolves.
function addTyping() {
  const m = el("div", "npchat-msg npchat-msg-assistant npchat-typing");
  m.innerHTML =
    '<span class="npchat-dot"></span>' +
    '<span class="npchat-dot"></span>' +
    '<span class="npchat-dot"></span>';
  els.messages.appendChild(m);
  scrollToBottom();
  return m;
}

// Swap the bouncing-dots placeholder for the real assistant answer (or an error
// note), drop the typing class so the bubble lays out normally, and keep the
// view pinned to the bottom so the fresh reply is always in focus.
function fillTyping(elm, text, isError) {
  const body = String(text || "").trim();
  if (!body && !isError) {
    // If a backend ever sends a side payload (for example an escalation card)
    // without visible assistant text, remove the typing placeholder instead of
    // converting it into an empty chat bubble.
    elm.remove();
    scrollToBottom();
    return;
  }
  elm.classList.remove("npchat-typing");
  if (isError) elm.textContent = text;
  else setMsgBody(elm, "assistant", body);
  scrollToBottom();
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
  scrollToBottom();
}

// How long the "switching to …" notice lingers before the re-asked answer
// starts streaming in. The backend's second call is the real latency mask; this
// brief, deliberate pause just makes the auto-switch legible (so it reads as an
// intentional hand-off, not a flash) instead of the answer popping instantly.
const SWITCH_NOTE_MS = 900;
// Cap chained auto-switches so a misbehaving model can't bounce the player
// across topics forever (the context-reset boundary makes this very unlikely,
// but the guard is cheap insurance).
const MAX_AUTO_SWITCHES = 2;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Seamlessly route the player to the topic the model flagged. Unlike the old
// one-tap button, this happens automatically: the backend already SUPPRESSED the
// ungrounded in-place answer (it was generated without the target topic's KB),
// so we never show it. We drop a persistent "switching to …" notice into the
// transcript (it stays in the history as the record of the hand-off — no button),
// switch the session topic, then re-ask the player's original question against
// the CORRECT KB and render that grounded answer. `depth` guards against loops.
async function autoSwitchTopic(suggested, originalText, depth) {
  if (!suggested || !suggested.slug) return;
  const title = suggested.title || suggested.slug;
  const note = el("div", "npchat-switch-note", tTopic("switching", title));
  els.messages.appendChild(note);
  scrollToBottom();
  // Switch the session's topic (loads the new KB + resets the prompt-history
  // boundary server-side); non-fatal if it fails — still attempt the re-ask.
  try {
    await selectTopic(suggested.slug);
  } catch (_) { /* non-fatal: still attempt the re-ask */ }
  await sleep(SWITCH_NOTE_MS);
  // The player's question is already in the transcript — re-ask it under the new
  // topic and stream the grounded answer into a fresh assistant bubble.
  const typing = addTyping();
  try {
    const data = await sendMessage(originalText);
    fillTyping(typing, data.reply || "");
    applyTurnExtras(data, originalText, depth + 1);
  } catch (e) {
    fillTyping(typing, t("sendError"), true);
  }
}

// ---------------------------------------------------------------------------
// Suggested-question bubbles + the resolved "finish chat" button
// ---------------------------------------------------------------------------
// The model returns up to three short follow-up questions (player's POV) to nudge
// the player toward the concrete KB answer their question is closest to. We render
// them as one-tap bubbles right above the input field — each on its own line —
// and tapping one sends it as the next message. When the model judges the question
// resolved we add a green "finish chat" button below them to steer the satisfied
// player toward closing. The whole strip is rebuilt every assistant turn and
// hidden while empty.
function clearSuggestions() {
  if (!els.suggestions) return;
  els.suggestions.innerHTML = "";
  els.suggestions.classList.add("npchat-hidden");
}

function renderSuggestions(list, closing, resolved) {
  clearSuggestions();
  const items = Array.isArray(list) ? list.slice(0, 2) : [];
  for (const q of items) {
    if (!q) continue;
    const b = el("button", "npchat-suggestion", q);
    b.addEventListener("click", () => submitText(q));
    els.suggestions.appendChild(b);
  }
  // Two finish affordances, one at a time:
  //  - resolved=true  -> the MODEL judged the chat done: show the green finish
  //    button (finishChat collapses the panel with a canned note).
  //  - else + closing -> the PLAYER may finish proactively: show the declarative
  //    closing bubble; tapping it sends a goodbye turn and then resolves.
  // The green button wins when both apply, so we never show two finish controls.
  if (resolved) {
    const f = el("button", "npchat-finish", t("finish"));
    f.addEventListener("click", finishChat);
    els.suggestions.appendChild(f);
  } else if (closing) {
    const c = el("button", "npchat-suggestion npchat-suggestion-closing", closing);
    c.addEventListener("click", () => finishWithClosing(closing));
    els.suggestions.appendChild(c);
  }
  if (els.suggestions.childNodes.length) {
    els.suggestions.classList.remove("npchat-hidden");
  }
}

// Apply the per-turn side payloads shared by a typed send and a topic resend:
// the escalation block, a cross-topic switch prompt, and the guide-to-KB
// bubbles / finish button.
function applyTurnExtras(data, originalText, depth = 0) {
  if (data.escalation && data.escalation.active) {
    // A hand-off ends the bot conversation: show the contact card, then interrupt
    // all further chatting. The session is now 'escalated' on the backend (it
    // would reject the next message with 409), so drop the input and the local
    // credentials — the player can only talk again by starting a NEW conversation.
    addEscalation(data.escalation);
    endConversation();
    scrollToBottom();
    return;
  }
  // The model routed the question to another topic: auto-switch to it and re-ask
  // there (the backend already suppressed the ungrounded in-place answer, so the
  // reply for THIS turn is empty and the typing bubble was already removed). The
  // depth guard stops a runaway chain of switches.
  if (data.suggested_topic && depth < MAX_AUTO_SWITCHES) {
    autoSwitchTopic(data.suggested_topic, originalText, depth);
    return;
  }
  // If a topic switch is in flight, do not also show guide bubbles: they would
  // invite the player to continue in the wrong topic. The backend already strips
  // them, but keep the widget defensive for older/mixed deployments.
  const canShowSuggestions = !data.suggested_topic;
  renderSuggestions(
    canShowSuggestions ? data.suggestions : [],
    canShowSuggestions ? data.closing_suggestion : null,
    canShowSuggestions && data.resolved,
  );
  // The follow-up bubbles render AFTER the reply was scrolled into view and sit
  // outside the scroll container, so they shrink the transcript's viewport and
  // would otherwise clip the bottom of the fresh answer. Re-pin once they're in.
  scrollToBottom();
}

// End the conversation from the resolved "finish chat" nudge: tell the backend to
// close the session (status='resolved' + admin event), drop the bubbles, leave a
// short closing note in the transcript, and collapse the panel so the satisfied
// player is gently taken to a closed chat. The close call is best-effort — the
// panel collapses regardless so the player is never stuck.
function finishChat() {
  clearSuggestions();
  const sessionId = state.sessionId;
  const closePromise = sessionId
    ? api("/api/chat/resolve", { auth: true, body: { session_id: sessionId } })
        .catch(() => { /* non-fatal: still close the panel below */ })
    : Promise.resolve();
  if (state.sessionId) {
    // Drop stale credentials immediately so any next open starts a fresh session
    // even if the close request is still in flight or the host keeps this widget
    // instance alive for a long time.
    resetSessionState();
  }
  closePromise.finally(() => { /* intentionally best-effort */ });
  addMessage("assistant", t("finished"));
  if (state.open) togglePanel();
}

// Interrupt the current conversation without collapsing the panel: hide the
// composer and drop the local session credentials so the visible transcript
// (escalation card or goodbye) stays readable, while any further chatting can
// only happen in a fresh session the next time the player opens the picker.
// Shared by escalation hand-offs and the player-driven closing bubble.
function endConversation() {
  clearSuggestions();
  if (els.inputRow) els.inputRow.classList.add("npchat-hidden");
  resetSessionState();
}

// The player tapped the declarative closing bubble ("Issue solved."). Send it as
// a normal turn so Nika gives a warm goodbye (generated, as before), then mark the
// session resolved — but show NO green finish button afterwards, since finishing
// is exactly what the player just chose. If that turn unexpectedly escalates, fall
// back to the escalation hand-off instead of closing.
async function finishWithClosing(text) {
  if (!text) return;
  clearSuggestions();
  addMessage("user", text);
  const typing = addTyping();
  let data;
  try {
    data = await sendMessage(text);
  } catch (e) {
    fillTyping(typing, t("sendError"), true);
    return;
  }
  fillTyping(typing, data.reply || "");
  if (data.escalation && data.escalation.active) {
    addEscalation(data.escalation);
    endConversation();
    scrollToBottom();
    return;
  }
  // Mark resolved one step early (best-effort) and end the conversation: the
  // goodbye stays on screen, and reopening starts a clean picker / fresh session.
  const sessionId = state.sessionId;
  if (sessionId) {
    api("/api/chat/resolve", { auth: true, body: { session_id: sessionId } })
      .catch(() => { /* non-fatal */ });
  }
  endConversation();
  scrollToBottom();
}

// ---------------------------------------------------------------------------
// Mobile full-screen sheet — VisualViewport-driven geometry
// ---------------------------------------------------------------------------
// Why not pure CSS? Two host-page-independent failures kept recurring:
//   1. "Opens only on part of the screen": a CSS media query keyed on
//      `max-width: 600px` matches the *CSS* pixel width, which the host page's
//      viewport meta tag controls — get it wrong and the query never fires, so
//      the panel keeps its 340×500 desktop size. Measuring the real viewport in
//      JS sidesteps the host page entirely.
//   2. "Input pushes half the widget off-screen": with `position: fixed` the
//      element is sized to the *layout* viewport. When the on-screen keyboard
//      opens, the layout viewport doesn't change but the *visual* viewport
//      shrinks and scrolls — so a fixed full-height sheet keeps its full height
//      and its bottom (the input row) ends up hidden behind the keyboard.
//
// The fix: pin the panel to `window.visualViewport` and set its exact
// top/left/width/height inline, re-syncing on every viewport resize/scroll. The
// sheet then always covers exactly the visible area, keyboard open or not.

// Treat as "mobile" using the real measured viewport, not a CSS media query, so
// an odd host-page viewport meta can't defeat the detection.
function isMobileViewport() {
  if (typeof window === "undefined") return false;
  const vv = window.visualViewport;
  const w = (vv && vv.width) || window.innerWidth || 0;
  const h = (vv && vv.height) || window.innerHeight || 0;
  return w <= 600 || h <= 480;
}

// Size & position the open sheet to exactly the currently-visible viewport.
function syncFullscreenGeometry() {
  if (!state.open || !state.fullscreen || !els.panel) return;
  const vv = window.visualViewport;
  const s = els.panel.style;
  s.position = "fixed";
  if (vv) {
    // offsetLeft/offsetTop track how far the visual viewport has been panned
    // (e.g. when the keyboard scrolls the page); pinning to them keeps the
    // sheet glued to the visible area instead of drifting off-screen.
    s.left = vv.offsetLeft + "px";
    s.top = vv.offsetTop + "px";
    s.width = vv.width + "px";
    s.height = vv.height + "px";
  } else {
    // No VisualViewport API: fall back to the layout viewport.
    s.left = "0px";
    s.top = "0px";
    s.width = window.innerWidth + "px";
    s.height = window.innerHeight + "px";
  }
  s.right = "auto";
  s.bottom = "auto";
  s.maxWidth = "none";
  s.maxHeight = "none";
}

function enterFullscreen() {
  state.fullscreen = true;
  els.panel.classList.add("npchat-fullscreen");
  els.root.classList.add("npchat-fullscreen-open");
  const vv = window.visualViewport;
  if (vv) {
    vv.addEventListener("resize", syncFullscreenGeometry);
    vv.addEventListener("scroll", syncFullscreenGeometry);
  }
  window.addEventListener("resize", syncFullscreenGeometry);
  window.addEventListener("orientationchange", syncFullscreenGeometry);
  syncFullscreenGeometry();
}

function exitFullscreen() {
  state.fullscreen = false;
  const vv = window.visualViewport;
  if (vv) {
    vv.removeEventListener("resize", syncFullscreenGeometry);
    vv.removeEventListener("scroll", syncFullscreenGeometry);
  }
  window.removeEventListener("resize", syncFullscreenGeometry);
  window.removeEventListener("orientationchange", syncFullscreenGeometry);
  if (els.panel) {
    els.panel.classList.remove("npchat-fullscreen");
    // Drop the inline geometry so the desktop CSS rules take over again.
    const s = els.panel.style;
    s.position = s.left = s.top = s.right = s.bottom = "";
    s.width = s.height = s.maxWidth = s.maxHeight = "";
  }
  if (els.root) els.root.classList.remove("npchat-fullscreen-open");
}

// ---------------------------------------------------------------------------
// flow handlers
// ---------------------------------------------------------------------------
// On phones the open panel is a full-screen sheet (see enterFullscreen above).
// Lock the host page's scroll behind it so the sheet truly owns the screen and
// the page underneath can't scroll through. No-op on desktop / large viewports.
function setBodyScrollLock(locked) {
  document.documentElement.style.overflow =
    locked && state.fullscreen ? "hidden" : "";
  document.body.style.overflow = locked && state.fullscreen ? "hidden" : "";
}

async function togglePanel() {
  state.open = !state.open;
  els.panel.classList.toggle("npchat-hidden", !state.open);
  if (state.open && isMobileViewport()) {
    enterFullscreen();
  } else if (!state.open) {
    exitFullscreen();
  }
  setBodyScrollLock(state.open);
  if (state.open) {
    // Every open is a FRESH conversation. Closing / leaving the widget abandons
    // the previous session (below + goBackToTopics), so re-opening must always
    // land on a clean topic picker backed by a brand-new session — never the
    // previous (possibly closed) conversation. `abandon` drops any leftover
    // session so the warm-up below mints a new one.
    resetToPicker({ abandon: true });
    // Paint the category buttons as fast as we can. If the speculative mount
    // prefetch already landed, this is instant; otherwise fetch them now —
    // either way it does NOT wait on reCaptcha or the session create.
    try {
      if (!state.topicsLoaded) await fetchTopics();
      renderTopics();
    } catch (e) {
      els.topics.innerHTML = "";
      addMessageToTopics(t("startError"));
    }
    // Warm up the (slower) reCaptcha + session create in the background so it's
    // ready by the time the player reads the list and taps a topic.
    ensureSession().catch(() => { /* surfaced when an action needs the token */ });
  } else {
    // Closing the widget abandons the current chat: drop the session credentials
    // so nothing stale is reused. The next open starts cleanly (above).
    resetSessionState();
  }
}

function addMessageToTopics(text) {
  els.topics.appendChild(el("div", "npchat-error", text));
}

async function onTopic(slug) {
  // The session (token) may still be warming up in the background — wait for it
  // here, the one place its absence would actually break the next request. A
  // failed session create is fatal (no token = no chat), so surface it.
  try {
    await ensureSession();
  } catch (e) {
    els.topics.innerHTML = "";
    addMessageToTopics(t("startError"));
    return;
  }
  try {
    await selectTopic(slug);
  } catch (_) { /* non-fatal: still allow chatting */ }
  state.topicChosen = true;
  els.topics.classList.add("npchat-hidden");
  els.messages.classList.remove("npchat-hidden");
  els.inputRow.classList.remove("npchat-hidden");
  els.back.classList.remove("npchat-hidden");
  state.greetingEl = addMessage("assistant", t("greeting"));
  els.input.focus();
}

// Return to the topic picker from inside a conversation. Tapping "back" ABANDONS
// the current chat: the session is dropped so picking a topic starts a brand-new
// one (matching "back / close / finish all end the current conversation"). This
// avoids reusing a session that may already be closed server-side — which used
// to silently 409 the first message of the "new" chat and leave the player stuck.
function goBackToTopics() {
  resetToPicker({ abandon: true });
  renderTopics();
  // Pre-warm the replacement session so the next topic tap is instant.
  ensureSession().catch(() => { /* surfaced when an action needs the token */ });
}

async function onSend() {
  const text = els.input.value.trim();
  if (!text) return;
  els.input.value = "";
  await submitText(text);
}

// Send one player turn (typed or tapped from a suggestion bubble). The old
// bubbles are stale the moment a new turn starts, so clear them up front; the
// fresh set (and any finish button) is rendered from the response.
async function submitText(text) {
  if (!text) return;
  clearSuggestions();
  addMessage("user", text);
  const typing = addTyping();
  try {
    const data = await sendMessage(text);
    fillTyping(typing, data.reply || "");
    applyTurnExtras(data, text);
  } catch (e) {
    fillTyping(typing, t("sendError"), true);
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
