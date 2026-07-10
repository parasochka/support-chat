// NowPlix support-chat floating widget — vanilla ES module, no build step.
//
// Embed contract (for the host site): one script tag with the product's widget
// key (issued in the admin Structure tab):
//   <script type="module" src="https://chat.example.com/widget.js"
//           data-widget-key="wk_..."></script>
// or import and call mount() with overrides:
//   import { mount } from "/widget.js";
//   mount({ API_BASE: "https://chat.example.com", WIDGET_KEY: "wk_..." });
//
// Owner tuning block — tweak without digging into the logic below.
export const CONFIG = {
  // Base URL of this service. "" = same origin as the page that loads the widget.
  API_BASE: "",
  // Public product identifier (multi-tenancy): tells the service WHICH casino
  // this widget belongs to — its topics, copy, prompt persona and OpenAI keys.
  // Resolution: window.NPCHAT_WIDGET_KEY -> the script tag's data-widget-key
  // (ES modules can't use document.currentScript, so the tag is found by the
  // attribute) -> "" (the service falls back to its default product).
  WIDGET_KEY:
    (typeof window !== "undefined" && window.NPCHAT_WIDGET_KEY) ||
    (typeof document !== "undefined" &&
      (document.querySelector("script[data-widget-key]") || { dataset: {} })
        .dataset.widgetKey) ||
    "",
  // Cloudflare Turnstile site key (the widget MUST be created as an INVISIBLE
  // widget in the Cloudflare dashboard — no challenge UI is ever shown).
  // Normally left "" — the widget adopts the PRODUCT's own site key served by
  // GET /api/chat/i18n (each client domain runs its own Turnstile widget,
  // configured in the admin Structure tab). A host page may still pin one
  // explicitly via mount({ TURNSTILE_SITE_KEY: "..." }).
  TURNSTILE_SITE_KEY: "",
  // Player context supplied by the host page via mount({ USER_CONTEXT: {...} }).
  // Empty by default: an anonymous embed sends no identity (in dev, the server's
  // admin-managed test profile stands in; in production the signed handshake is
  // the trusted source anyway). `language` is the account/profile language — the
  // strongest chrome-language signal, applied on the FIRST paint before any
  // network round-trip, so a Russian account on an English browser opens in
  // Russian with no flicker.
  USER_CONTEXT: {},
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
// These are the BAKED-IN defaults so the first paint never waits on the
// network; fetchI18n() then merges the server-resolved copy (the admin
// Translations tab) over them — including languages added beyond this set.
// House style (matches Nika's own formatting rules): straight quotes only, no
// guillemets «», no curly quotes, no em dashes.
const I18N = {
  en: { support: "Support", topics: "What can we help you with?", other: "Other",
        back: "Back to topics",
        greeting: "Hi, I'm Nika! How can I help you?", placeholder: "Type your message…",
        send: "Send", launcher: "Open support chat", close: "Close chat",
        startError: "Could not start chat. Please try again later.",
        sendError: "Something went wrong. Please try again.",
        switching: 'Looks like your question is about "{topic}", switching you there…',
        switchStuck: "I couldn't settle on the right topic for this question. Please rephrase it in a bit more detail.",
        finish: "End chat", finished: "Chat ended. Thanks for reaching out!" },
  ru: { support: "Поддержка", topics: "Чем мы можем помочь?", other: "Другое",
        back: "К выбору темы",
        greeting: "Привет, я Ника, чем могу тебе помочь?", placeholder: "Введите сообщение…",
        send: "Отправить", launcher: "Открыть чат поддержки", close: "Закрыть чат",
        startError: "Не удалось начать чат. Попробуйте позже.",
        sendError: "Что-то пошло не так. Попробуйте ещё раз.",
        switching: 'Похоже, твой вопрос про "{topic}", переключаю тему…',
        switchStuck: "Мне не удалось подобрать подходящую тему для этого вопроса. Пожалуйста, переформулируй его чуть подробнее.",
        finish: "Завершить чат", finished: "Чат завершён. Спасибо за обращение!" },
  es: { support: "Soporte", topics: "¿En qué podemos ayudarte?", other: "Otro",
        back: "Volver a los temas",
        greeting: "¡Hola, soy Nika! ¿En qué puedo ayudarte?", placeholder: "Escribe tu mensaje…",
        send: "Enviar", launcher: "Abrir chat de soporte", close: "Cerrar chat",
        startError: "No se pudo iniciar el chat. Inténtalo más tarde.",
        sendError: "Algo salió mal. Inténtalo de nuevo.",
        switching: 'Parece que tu pregunta es sobre "{topic}", cambiando de tema…',
        switchStuck: "No pude encontrar el tema adecuado para esta pregunta. Por favor, reformúlala con un poco más de detalle.",
        finish: "Finalizar chat", finished: "Chat finalizado. ¡Gracias por contactarnos!" },
  tr: { support: "Destek", topics: "Size nasıl yardımcı olabiliriz?", other: "Diğer",
        back: "Konulara dön",
        greeting: "Merhaba, ben Nika! Sana nasıl yardımcı olabilirim?",
        placeholder: "Mesajınızı yazın…", send: "Gönder",
        launcher: "Destek sohbetini aç", close: "Sohbeti kapat",
        startError: "Sohbet başlatılamadı. Lütfen daha sonra tekrar deneyin.",
        sendError: "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
        switching: 'Görünüşe göre sorunuz "{topic}" ile ilgili, konuyu değiştiriyorum…',
        switchStuck: "Bu soru için uygun konuyu bulamadım. Lütfen biraz daha ayrıntılı şekilde yeniden yazar mısın?",
        finish: "Sohbeti bitir", finished: "Sohbet sona erdi. Bize ulaştığınız için teşekkürler!" },
  pt: { support: "Suporte", topics: "Como podemos ajudar?", other: "Outro",
        back: "Voltar aos tópicos",
        greeting: "Oi, eu sou a Nika! Como posso te ajudar?", placeholder: "Digite sua mensagem…",
        send: "Enviar", launcher: "Abrir chat de suporte", close: "Fechar chat",
        startError: "Não foi possível iniciar o chat. Tente novamente mais tarde.",
        sendError: "Algo deu errado. Tente novamente.",
        switching: 'Parece que sua pergunta é sobre "{topic}", mudando de tópico…',
        switchStuck: "Não consegui encontrar o tópico certo para essa pergunta. Por favor, reformule com um pouco mais de detalhes.",
        finish: "Encerrar chat", finished: "Chat encerrado. Obrigado pelo contato!" },
};

// Per-topic emoji so each menu item reads at a glance. Keyed by the backend
// topic slug; unknown slugs fall back to a neutral bubble so a newly-added
// topic still renders something. "other" is a normal catalogue topic (served
// last by the backend); renderTopics() only gives it its distinct styling.
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
// panel is ever painted — from the account/profile language (the strongest
// signal, when the host supplied one), then the browser language, falling back
// to English. This is the same chain the backend resolves, so chrome and
// answers agree from turn one with no post-/session flicker. It can later follow
// the conversation if the player switches language mid-chat (see maybeSwitchLang).
function resolveLang() {
  return (
    baseLang(CONFIG.USER_CONTEXT && CONFIG.USER_CONTEXT.language) ||
    baseLang(CONFIG.LOCALE) ||
    "en"
  );
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
  // In-flight promise for the (slow) Turnstile + session create, started in the
  // background on open so it never blocks the first paint. ensureSession() awaits
  // it lazily before any action that actually needs a token.
  sessionPromise: null,
  // In-flight promise for the whole background conversation setup started by a
  // topic tap (session create + topic select). The chat view and the greeting
  // paint IMMEDIATELY on tap; sendMessage() awaits this so the player's first
  // message transparently waits for the setup instead of failing without a token.
  setupPromise: null,
  // Mount-time /i18n fetch (chrome copy + the product's Turnstile site key);
  // turnstileToken() awaits it when the key hasn't arrived yet.
  i18nPromise: null,
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
  // Guards against double-tapping a topic button while the session is still
  // warming up: onTopic awaits ensureSession(), and the buttons stay clickable
  // during that wait, so a second tap used to fire a second onTopic and paint a
  // second greeting bubble. Set the instant the first tap lands, cleared if the
  // selection fails.
  topicSelecting: false,
  open: false,
  // True while the mobile full-screen sheet is active (geometry driven by JS).
  fullscreen: false,
  greetingEl: null,
  // Conversation generation counter. Bumped every time the current conversation
  // is torn down (back / close / finish / escalation), so an in-flight /message
  // response from an ABANDONED conversation can be recognized and dropped
  // instead of clobbering the new one (ending its session, injecting stale
  // switch notes / suggestion bubbles).
  generation: 0,
  // True while a player turn is in flight; blocks a second concurrent send
  // (Enter mashing, suggestion-bubble taps) that would 429 on the cooldown and
  // interleave replies out of order.
  sending: false,
};

// ---------------------------------------------------------------------------
// Cloudflare Turnstile helper (no-op when no site key configured)
// ---------------------------------------------------------------------------
// The Turnstile widget is created as INVISIBLE in the Cloudflare dashboard, so
// it never shows any challenge UI — it is rendered into a hidden container and
// hands back a token via callback. A blocker (or a region where Cloudflare is
// unreachable) can silently drop the challenges.cloudflare.com request — then
// neither onload nor onerror ever fires and a stalled render never settles,
// wedging session creation forever. Every step therefore races a timeout and
// degrades to a null token; the backend treats a missing token as "skip the
// check" (fail-open by design — the other anti-spam layers still apply).
const TURNSTILE_TIMEOUT_MS = 8000;

function withTimeout(promise, ms, fallback) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    promise.then(
      (v) => { clearTimeout(timer); resolve(v); },
      () => { clearTimeout(timer); resolve(fallback); }
    );
  });
}

function loadTurnstile() {
  return new Promise((resolve) => {
    if (window.turnstile) return resolve(window.turnstile);
    const existing = document.querySelector("script[data-npchat-turnstile]");
    if (existing) {
      // Already injected (e.g. the mount-time pre-load); wait for it.
      existing.addEventListener("load", () => resolve(window.turnstile || null));
      existing.addEventListener("error", () => resolve(null));
      return;
    }
    const s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true;
    s.setAttribute("data-npchat-turnstile", "1");
    s.onload = () => resolve(window.turnstile || null);
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
  });
}

async function turnstileToken(action) {
  // The product's site key may still be in flight (it arrives with /i18n);
  // wait for that fetch before concluding there is no captcha to run.
  if (!CONFIG.TURNSTILE_SITE_KEY && state.i18nPromise) {
    await state.i18nPromise;
  }
  if (!CONFIG.TURNSTILE_SITE_KEY) return null;
  const ts = await withTimeout(loadTurnstile(), TURNSTILE_TIMEOUT_MS, null);
  if (!ts) return null;
  // Render an invisible widget into an off-screen container; the token arrives
  // via callback. Tokens are single-use, so a fresh container is rendered per
  // token and removed afterwards.
  const holder = document.createElement("div");
  holder.style.position = "fixed";
  holder.style.left = "-9999px";
  holder.style.top = "0";
  document.body.appendChild(holder);
  let widgetId = null;
  const exec = new Promise((resolve) => {
    try {
      widgetId = ts.render(holder, {
        sitekey: CONFIG.TURNSTILE_SITE_KEY,
        action,
        callback: (token) => resolve(token || null),
        "error-callback": () => resolve(null),
        "unsupported-callback": () => resolve(null),
      });
      if (widgetId === undefined || widgetId === null) resolve(null);
    } catch (_) {
      resolve(null);
    }
  });
  const token = await withTimeout(exec, TURNSTILE_TIMEOUT_MS, null);
  try {
    if (widgetId !== null && widgetId !== undefined && ts.remove) ts.remove(widgetId);
  } catch (_) { /* best-effort cleanup */ }
  holder.remove();
  return token;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------
// Hard client-side timeout so a stalled connection (mobile network drop
// mid-request) rejects into the normal error handling instead of leaving the
// typing dots up for the browser's own multi-minute timeout.
const API_TIMEOUT_MS = 45000;

function apiTimeoutSignal() {
  try {
    if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) {
      return AbortSignal.timeout(API_TIMEOUT_MS);
    }
    const ctl = new AbortController();
    setTimeout(() => ctl.abort(), API_TIMEOUT_MS);
    return ctl.signal;
  } catch (_) {
    return undefined; // very old browser: no timeout, previous behaviour
  }
}

async function api(path, { method = "POST", body = null, auth = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const res = await fetch(`${CONFIG.API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: apiTimeoutSignal(),
  });
  let data = null;
  try { data = await res.json(); } catch (_) { data = {}; }
  return { ok: res.ok, status: res.status, data };
}

// Fetch just the topic catalogue — no session, token, or Turnstile — so the
// panel can paint the category buttons instantly on open. Cheap + cacheable;
// safe to call speculatively on mount. We pass the already-resolved chrome
// language so the titles come back in the right language on the first paint —
// the catalogue must follow `state.lang`, never redefine it.
async function fetchTopics() {
  const qs = new URLSearchParams({ lang: state.lang });
  if (CONFIG.WIDGET_KEY) qs.set("widget_key", CONFIG.WIDGET_KEY);
  const { ok, data } = await api(`/api/chat/topics?${qs.toString()}`,
                                 { method: "GET" });
  if (!ok) throw new Error("topics fetch failed");
  state.topics = data.topics || [];
  state.topicsLoaded = true;
  applyStaticLabels();
}

// Fetch the server-resolved chrome copy (admin Translations tab > built-in
// defaults) and merge it over the baked-in I18N. A language added from the
// admin beyond the baked-in set becomes a valid chrome language here (its
// missing keys inherit English via the server's own fallback chain). Cheap,
// cacheable, session-free; failures are non-fatal — the baked-in copy stands.
async function fetchI18n() {
  const qs = CONFIG.WIDGET_KEY
    ? `?widget_key=${encodeURIComponent(CONFIG.WIDGET_KEY)}` : "";
  const { ok, data } = await api(`/api/chat/i18n${qs}`, { method: "GET" });
  if (!ok || !data || !data.strings) return;
  // Adopt the product's Turnstile site key (served with the copy) unless the
  // host page pinned its own — then pre-load the script so the first topic tap
  // doesn't pay for it.
  if (!CONFIG.TURNSTILE_SITE_KEY && data.turnstile_site_key) {
    CONFIG.TURNSTILE_SITE_KEY = data.turnstile_site_key;
    loadTurnstile();
  }
  for (const [code, dict] of Object.entries(data.strings)) {
    I18N[code] = Object.assign({}, I18N[code] || I18N.en, dict);
  }
  // A language ADDED from the admin (beyond the baked-in set) only becomes
  // resolvable now that its strings arrived. If the first paint fell back to
  // English because the browser locale wasn't baked in, adopt it — but only
  // before any conversation starts, so an active chat never flips mid-turn.
  if (!state.topicChosen) {
    const wanted = resolveLang();
    if (wanted !== state.lang) {
      state.lang = wanted;
      fetchTopics().catch(() => { /* titles refresh on next open */ });
    }
  }
  // Re-apply anything already painted from the defaults.
  applyStaticLabels();
  if (state.greetingEl) state.greetingEl.textContent = t("greeting");
  if (els.topics && !els.topics.classList.contains("npchat-hidden")
      && state.topicsLoaded) {
    renderTopics();
  }
}

// Kick off (once) and await the background session create. Anything that needs
// a valid token — picking a topic, sending — funnels through here so it
// transparently waits for the in-flight session instead of starting its own.
// Errors propagate so callers can surface a "couldn't start" message.
function ensureSession() {
  if (state.sessionId) return Promise.resolve();
  if (!state.sessionPromise) {
    // Don't cache a REJECTED promise: a transient createSession failure (network
    // blip, a session-create rate-limit 429, a Turnstile hiccup) must not wedge
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
  state.generation += 1;
  state.sending = false;
  state.sessionId = null;
  state.token = null;
  state.sessionPromise = null;
  state.setupPromise = null;
  state.topicChosen = false;
  state.topicSelecting = false;
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
  state.topicSelecting = false;
  els.topics.classList.remove("npchat-hidden");
}

async function createSession() {
  const token = await turnstileToken("chat_session");
  const { ok, data } = await api("/api/chat/session", {
    body: {
      consumer: "web",
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
      turnstile_token: token,
      // Names the product (casino) this widget belongs to; empty = the
      // service's default product (single-product deployments).
      widget_key: CONFIG.WIDGET_KEY || null,
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
  const { ok } = await api("/api/chat/topic", {
    auth: true,
    body: { session_id: state.sessionId, topic_slug: slug },
  });
  if (!ok) throw new Error("topic select failed");
  state.topicChosen = true;
}

async function sendMessage(text, closing = false) {
  // The conversation setup (session create + topic select) runs in the
  // background after the topic tap so the chat opens instantly; the first
  // message just waits for it here (usually already settled by the time the
  // player finishes typing). A failed setup rejects through to the caller's
  // error handling.
  if (state.setupPromise) await state.setupPromise;
  const { ok, data, status } = await api("/api/chat/message", {
    auth: true,
    body: { session_id: state.sessionId, text, closing },
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
  closeBtn.setAttribute("aria-label", t("close"));
  closeBtn.addEventListener("click", togglePanel);
  headerRight.appendChild(closeBtn);
  header.appendChild(headerRight);

  const body = el("div", "npchat-body");
  const topics = el("div", "npchat-topics");
  const messages = el("div", "npchat-messages npchat-hidden");
  // Announce new assistant turns to assistive tech without stealing focus.
  messages.setAttribute("role", "log");
  messages.setAttribute("aria-live", "polite");

  // One-tap "guide-to-KB" question bubbles (+ the resolved "finish chat" button)
  // sit just above the input field. Populated per assistant turn from the
  // response's `suggestions` / `resolved`; hidden whenever it's empty.
  const suggestions = el("div", "npchat-suggestions npchat-hidden");

  const inputRow = el("div", "npchat-inputrow npchat-hidden");
  const input = el("input", "npchat-input");
  input.type = "text";
  input.placeholder = t("placeholder");
  input.addEventListener("keydown", (ev) => {
    // isComposing: don't fire on the Enter that confirms an IME candidate
    // (Japanese/Chinese/Korean and some Android keyboards) — that would send
    // the half-composed text.
    if (ev.key === "Enter" && !ev.isComposing && ev.keyCode !== 229) onSend();
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
  // touches no Turnstile or DB, so doing it on mount is cheap insurance.
  fetchTopics().catch(() => { /* the open handler retries if this missed */ });
  // Merge the admin-edited chrome copy over the baked-in defaults (non-fatal).
  // The promise is kept: the per-product Turnstile site key rides in this
  // response, so turnstileToken() awaits it when no key is known yet — a
  // topic tap racing the fetch must not mint a token-less session.
  state.i18nPromise = fetchI18n().catch(() => { /* baked-in copy stands */ });
  // Pre-load the Turnstile script too: it's the slowest piece of the session
  // create (a third-party script fetch), and without this it only started
  // loading when the player tapped a topic — adding seconds before the first
  // message could be sent. Loading it at mount costs nothing when unused
  // (fetchI18n also kicks it off once the product's site key arrives).
  if (CONFIG.TURNSTILE_SITE_KEY) loadTurnstile();
}

// Re-apply chrome strings. Idempotent, and re-run whenever the language changes
// (initial paint is the browser language; later it may follow the conversation).
function applyStaticLabels() {
  if (!els.root) return;
  els.launcher.setAttribute("aria-label", t("launcher"));
  const title = els.panel.querySelector(".npchat-title");
  if (title) title.textContent = t("support");
  if (els.back) els.back.setAttribute("aria-label", t("back"));
  const closeBtn = els.panel.querySelector(".npchat-close");
  if (closeBtn) closeBtn.setAttribute("aria-label", t("close"));
  els.input.placeholder = t("placeholder");
  els.input.setAttribute("aria-label", t("placeholder"));
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
  // The catalogue is served complete — "Other" is a normal topic (the server
  // sorts it last), with its own per-language title like every other topic.
  // It is the always-available escape hatch, so it keeps its distinct styling
  // (purple outline + AI icon) and never blends into the list above.
  let hasOther = false;
  for (const topic of state.topics) {
    const isOther = topic.slug === "other";
    if (isOther) hasOther = true;
    els.topics.appendChild(topicButton(
      topic.slug, topic.title, isOther ? "npchat-topic-other" : null));
  }
  // Safety net: a catalogue without its own "other" row (should not happen —
  // every product ships one) still gets the escape hatch, localized client-side.
  if (!hasOther) {
    els.topics.appendChild(topicButton("other", t("other"), "npchat-topic-other"));
  }
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
    // Strip the private-use sentinels renderInline() stashes tokens behind, so
    // a literal U+E000/U+E001 in the model/KB text can't forge a token
    // reference and corrupt the rendered output.
    .replace(/[\uE000\uE001]/g, "")
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
  m.setAttribute("role", "status");
  m.setAttribute("aria-label", "…");
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

// The contact URL is admin-entered (per-product Translations) — enforce the
// same scheme allow-list as renderMarkdown links so a semi-trusted tenant
// admin can never plant a javascript:/data: URL in another origin's page
// (tg: covers the retention-bot deeplink hand-off).
function isSafeButtonUrl(url) {
  return /^(https?:|mailto:|tg:)/i.test(String(url).trim());
}

function addEscalation(esc) {
  const wrap = el("div", "npchat-escalation");
  if (esc.message) wrap.appendChild(el("div", "npchat-esc-msg", esc.message));
  if (esc.button && esc.button.url && isSafeButtonUrl(esc.button.url)) {
    const a = el("a", "npchat-esc-btn", esc.button.label || "Contact support");
    a.href = esc.button.url;
    a.target = "_blank";
    a.rel = "noopener";
    wrap.appendChild(a);
  } else if (esc.button) {
    // Operator misconfiguration (empty/unsafe contact URL): log the hint for
    // the developer console only — never leak admin guidance to the player.
    try {
      console.warn(
        "npchat: contact button URL missing or unsafe - set contact_url in " +
        "the admin Translations tab");
    } catch (_) { /* no console */ }
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
  const gen = state.generation;
  const title = suggested.title || suggested.slug;
  const note = el("div", "npchat-switch-note", tTopic("switching", title));
  els.messages.appendChild(note);
  scrollToBottom();
  // Switch the session's topic (loads the new KB + resets the prompt-history
  // boundary server-side). A FAILED switch must not re-ask: the question would
  // run against the OLD topic's KB, the model would route again, and the loop
  // would only die at the depth guard after two wasted calls.
  try {
    await selectTopic(suggested.slug);
  } catch (_) {
    if (gen !== state.generation) return; // conversation was abandoned
    addMessage("assistant", t("switchStuck"));
    return;
  }
  await sleep(SWITCH_NOTE_MS);
  if (gen !== state.generation) return; // conversation was abandoned mid-switch
  // The player's question is already in the transcript — re-ask it under the new
  // topic and stream the grounded answer into a fresh assistant bubble.
  const typing = addTyping();
  try {
    const data = await sendMessage(originalText);
    if (gen !== state.generation) { typing.remove(); return; }
    fillTyping(typing, data.reply || "");
    applyTurnExtras(data, originalText, depth + 1);
  } catch (e) {
    if (gen !== state.generation) { typing.remove(); return; }
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
    addEscalation(data.escalation);
    // Two strengths of hand-off (escalation.final from the backend):
    //  - final (default): the session is closed server-side (a next message
    //    would 409) — hide the composer and drop the local credentials; the
    //    player can only talk again by starting a NEW conversation.
    //  - soft (final === false): a keyword trigger showed the contact card but
    //    the session stays OPEN — keep the composer so a fuzzy keyword false
    //    positive doesn't kill a live conversation.
    if (data.escalation.final === false) {
      scrollToBottom();
      return;
    }
    endConversation();
    scrollToBottom();
    return;
  }
  // The model routed the question to another topic: auto-switch to it and re-ask
  // there (the backend already suppressed the ungrounded in-place answer, so the
  // reply for THIS turn is empty and the typing bubble was already removed). The
  // depth guard stops a runaway chain of switches.
  if (data.suggested_topic) {
    if (depth < MAX_AUTO_SWITCHES) {
      autoSwitchTopic(data.suggested_topic, originalText, depth);
      return;
    }
    // Auto-switch limit reached and the backend suppressed the in-place answer:
    // without this fallback the turn would end with NO reply at all and the
    // chat would look frozen. Ask the player to rephrase instead.
    addMessage("assistant", t("switchStuck"));
    scrollToBottom();
    return;
  }
  renderSuggestions(data.suggestions, data.closing_suggestion, data.resolved);
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
    // closing=true: the player is ending the chat, so the backend prompts Nika
    // for a pure goodbye (no follow-up that would reopen the conversation).
    data = await sendMessage(text, true);
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
    // either way it does NOT wait on Turnstile or the session create.
    try {
      if (!state.topicsLoaded) await fetchTopics();
      renderTopics();
    } catch (e) {
      els.topics.innerHTML = "";
      addMessageToTopics(t("startError"));
    }
    // NOTE: the session is created LAZILY — only when the player actually picks
    // a topic (onTopic -> ensureSession). Opening and closing the panel used to
    // mint a DB session (and burn the per-IP session budget) for visitors who
    // never engaged; those "zero" sessions no longer exist at all.
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
  // Ignore repeat taps (the flags are set synchronously below, so a double tap
  // can no longer mint a duplicate greeting bubble).
  if (state.topicSelecting || state.topicChosen) return;
  state.topicSelecting = true;
  state.topicChosen = true;
  // Paint the conversation view IMMEDIATELY — the greeting bubble is canned and
  // client-side, so nothing about it needs the network. The slow parts
  // (Turnstile + session create + topic select) run in the background below;
  // the player can already read the greeting and start typing, and their first
  // send just awaits the setup (sendMessage). Previously this whole function
  // awaited the session create FIRST, so tapping a category left the picker
  // frozen for seconds before the chat appeared.
  els.topics.classList.add("npchat-hidden");
  els.messages.classList.remove("npchat-hidden");
  els.inputRow.classList.remove("npchat-hidden");
  els.back.classList.remove("npchat-hidden");
  state.greetingEl = addMessage("assistant", t("greeting"));
  els.input.focus();
  const setup = (async () => {
    // A failed session create is fatal (no token = no chat) and propagates; a
    // failed topic select is non-fatal (the chat still works, untopiced).
    await ensureSession();
    try {
      await selectTopic(slug);
    } catch (_) { /* non-fatal: still allow chatting */ }
  })();
  state.setupPromise = setup;
  try {
    await setup;
    state.topicSelecting = false;
  } catch (e) {
    // Surface the failure only if the player is still in THIS conversation
    // attempt (they may have gone back / closed while the setup was in flight).
    if (state.setupPromise !== setup) return;
    resetToPicker({ abandon: true });
    els.topics.innerHTML = "";
    addMessageToTopics(t("startError"));
  }
}

// Return to the topic picker from inside a conversation. Tapping "back" ABANDONS
// the current chat: the session is dropped so picking a topic starts a brand-new
// one (matching "back / close / finish all end the current conversation"). This
// avoids reusing a session that may already be closed server-side — which used
// to silently 409 the first message of the "new" chat and leave the player stuck.
function goBackToTopics() {
  resetToPicker({ abandon: true });
  renderTopics();
  // No session pre-warm here: like the open handler, the replacement session is
  // created lazily on the next topic tap, so backing out to the picker and
  // leaving does not mint an unused DB session.
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
  if (!text || state.sending) return;
  state.sending = true;
  if (els.sendBtn) els.sendBtn.disabled = true;
  const gen = state.generation;
  clearSuggestions();
  addMessage("user", text);
  const typing = addTyping();
  try {
    const data = await sendMessage(text);
    // The conversation may have been abandoned (back / close / new topic) while
    // this response was in flight — drop it instead of clobbering the new chat.
    if (gen !== state.generation) { typing.remove(); return; }
    fillTyping(typing, data.reply || "");
    applyTurnExtras(data, text);
  } catch (e) {
    if (gen !== state.generation) { typing.remove(); return; }
    fillTyping(typing, t("sendError"), true);
  } finally {
    if (gen === state.generation) state.sending = false;
    if (els.sendBtn) els.sendBtn.disabled = false;
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
