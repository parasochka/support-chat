"""The MCP tool catalogue — ONE declarative table, no logic.

Every tool is a thin, named projection of an existing `/admin/*` endpoint: the
MCP server never talks to Postgres and never re-implements an authorization
rule. It sends `Authorization: Bearer sak_…` to the deployed service and lets
`app/api/admin_auth.require_admin` + the scope helpers decide what the key may see
and touch (invariant: authorization decisions live at the API choke points, not
in a client).

Why a curated list and not one tool per endpoint: the admin API has ~90 routes,
and every tool schema sits in the model's context for the whole session. Twenty
task-shaped tools plus the `admin_get` escape hatch cover the real work
("why is the bot answering like that", "what broke last night", "tune this
knob") at a fraction of the context cost, and each description can say what the
endpoint is FOR instead of what it is named.

This module is imported by BOTH the server (mcp_server/server.py) and the admin
API (`GET /admin/mcp/manifest`, which renders the System → MCP page), so the
panel's tool list can never drift from what the server actually exposes. It
must therefore stay pure stdlib — no httpx, no config, no db.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

SERVER_NAME = "support-admin"
SERVER_VERSION = "1.0.0"

# Groups are presentation only (the admin page renders one section per group).
GROUPS = (
    ("diagnostics", "Diagnostics — logs, audit trail, health"),
    ("analytics", "Analytics — dashboards and conversation lists"),
    ("prompt", "Prompt — the assembled prompt and its brand variables"),
    ("content", "Content — knowledge base, translations, site map"),
    ("retention", "Retention — the Telegram bot and its proactive agent"),
    ("settings", "Settings — the hot-reloaded runtime knobs"),
    ("raw", "Raw — the escape hatch for anything not listed above"),
)


@dataclass(frozen=True)
class Param:
    """One tool argument, and where it rides in the HTTP request."""
    name: str
    type: str = "string"               # JSON Schema primitive
    description: str = ""
    required: bool = False
    # query | path | body | path_body (sent in both, for endpoints that
    # cross-check them) | local (consumed by the server, never sent).
    location: str = "query"
    enum: Optional[tuple] = None


@dataclass(frozen=True)
class Tool:
    """One MCP tool = one admin endpoint plus how to call it."""
    name: str
    group: str
    description: str
    method: str
    path: str
    params: tuple = ()
    # How the acting product is passed. "query" adds an optional product_id
    # query param (defaulting to SUPPORT_ADMIN_PRODUCT_ID); "query!" makes it
    # mandatory (the endpoint 422s without one); "body" puts it in the JSON
    # body; None means the endpoint is not product-scoped.
    product: Optional[str] = None
    writes: bool = False
    # Tools whose payload can carry player PII (names, emails, transcripts) get
    # a `redact_pii` argument, defaulting to on.
    redactable: bool = False
    # Free-text caveat surfaced in the tool description and the admin page.
    note: str = ""
    # The generic escape hatch: `path` comes from the arguments instead of the
    # spec, so the server builds the request differently.
    raw: bool = False

    @property
    def all_params(self) -> tuple:
        out = list(self.params)
        if self.product in ("query", "query!"):
            out.append(Param(
                "product_id", "integer",
                "Product (casino) id — see the `structure` tool. Defaults to "
                "the server's SUPPORT_ADMIN_PRODUCT_ID when set.",
                required=False))
        elif self.product == "body":
            out.append(Param(
                "product_id", "integer",
                "Product (casino) id — see the `structure` tool.",
                required=False, location="body"))
        if self.redactable:
            out.append(Param(
                "redact_pii", "boolean",
                "Mask player names/emails in the result (default true). Pass "
                "false only when the personal data itself is what you are "
                "debugging.",
                required=False, location="local"))
        return tuple(out)

    def input_schema(self) -> dict:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.all_params:
            schema: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                schema["enum"] = list(p.enum)
            if p.name == "params" and p.type == "object":
                schema["additionalProperties"] = True
            props[p.name] = schema
            if p.required:
                required.append(p.name)
        out: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            out["required"] = required
        return out

    def full_description(self) -> str:
        parts = [self.description]
        if self.note:
            parts.append(self.note)
        if self.writes:
            parts.append("WRITES: this changes live behaviour for real players.")
        return " ".join(parts)


# --- shared parameter shorthands -------------------------------------------
_FROM = Param("from", "string",
              "Range start, YYYY-MM-DD or ISO-8601 (default: 30 days ago).")
_TO = Param("to", "string",
            "Range end, YYYY-MM-DD (inclusive of that whole day) or ISO-8601.")
_PAGE = Param("page", "integer", "1-based page number (default 1).")
_PAGE_SIZE = Param("page_size", "integer", "Rows per page (default 50).")


TOOLS: tuple = (
    # ------------------------------------------------------------------ raw
    Tool(
        name="structure",
        group="analytics",
        description="List the partners and products this key can reach, with "
                    "their ids, slugs and widget keys. Call this first when you "
                    "need a product_id for the other tools.",
        method="GET", path="/admin/structure",
    ),

    # ---------------------------------------------------------- diagnostics
    Tool(
        name="logs",
        group="diagnostics",
        description="Recent application runtime logs (newest first) — the "
                    "in-app mirror of the Railway log stream: escalations, "
                    "OpenAI failovers, model errors, retention agent decisions.",
        method="GET", path="/admin/logs",
        params=(
            Param("level", "string",
                  "Minimum level to return, e.g. WARNING or ERROR.",
                  enum=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
            Param("q", "string", "Substring filter over the message text."),
            Param("limit", "integer", "Rows to return, max 500 (default 100)."),
            Param("before_id", "integer",
                  "Return rows older than this id (pagination)."),
        ),
        note="Needs a GLOBAL-scope key: app_logs is one deploy-wide table with "
             "no product column, so a product-scoped key is refused (403).",
    ),
    Tool(
        name="audit",
        group="diagnostics",
        description="The admin action audit trail — who changed what and when, "
                    "including changes made through this MCP server (they are "
                    "recorded under the key's name).",
        method="GET", path="/admin/audit",
        params=(
            Param("q", "string", "Substring filter over the action label."),
            Param("limit", "integer", "Rows to return, max 500 (default 100)."),
            Param("before_id", "integer", "Return rows older than this id."),
        ),
        product="query",
    ),
    Tool(
        name="overview",
        group="diagnostics",
        description="Support-chat KPIs for a date range: sessions, escalations, "
                    "resolution rate, AI call count, failed calls, average "
                    "latency and cost. Telegram/retention traffic is excluded.",
        method="GET", path="/admin/overview",
        params=(_FROM, _TO),
        product="query",
    ),

    # ------------------------------------------------------------ analytics
    Tool(
        name="sessions",
        group="analytics",
        description="Support chat sessions for a range, with topic, language, "
                    "status, message count and cost. Use it to find a session "
                    "id worth reading in full.",
        method="GET", path="/admin/sessions",
        params=(
            _FROM, _TO,
            Param("topic", "string", "Filter by topic slug."),
            Param("lang", "string", "Filter by language code."),
            Param("status", "string", "open | escalated | resolved."),
            Param("escalated", "boolean", "Only escalated (or only not)."),
            Param("q", "string", "Substring search over the messages."),
            Param("min_messages", "integer", "Only sessions with at least N messages."),
            _PAGE,
        ),
        product="query", redactable=True,
    ),
    Tool(
        name="session_transcript",
        group="analytics",
        description="The full transcript of one session: every message, the "
                    "per-turn AI cost, and the topic-switch markers.",
        method="GET", path="/admin/session/{session_id}",
        params=(Param("session_id", "string", "Session UUID.",
                      required=True, location="path"),),
        redactable=True,
    ),
    Tool(
        name="unresolved",
        group="analytics",
        description="The triage queue: engaged sessions that ended escalated or "
                    "were abandoned, grouped by topic, each with its first "
                    "message. The fastest read on what the KB fails to answer.",
        method="GET", path="/admin/unresolved",
        params=(_FROM, _TO),
        product="query", redactable=True,
    ),

    # --------------------------------------------------------------- prompt
    Tool(
        name="effective_prompt",
        group="prompt",
        description="The WHOLE support prompt exactly as assembled for this "
                    "product — Layer 1 core + static directives + the KB block, "
                    "and the Layer-3 user message — with the brand variables "
                    "already substituted. Read this before diagnosing behaviour.",
        method="GET", path="/admin/effective-prompt",
        product="query",
    ),
    Tool(
        name="prompt_variables",
        group="prompt",
        description="The support persona's brand variables (persona name, brand, "
                    "products, tone of voice, support scope): the raw override, "
                    "the shipped default and the resolved value per key.",
        method="GET", path="/admin/prompt-variables",
        product="query",
    ),
    Tool(
        name="set_prompt_variables",
        group="prompt",
        description="Replace the support persona's brand variables for this "
                    "product. Send the whole {key: value} map; an empty value "
                    "falls back to the shipped default. Values must be English.",
        method="PUT", path="/admin/prompt-variables",
        params=(Param("value", "object",
                      "Full {variable_key: text} map to store.",
                      required=True, location="body"),),
        product="query", writes=True,
    ),
    Tool(
        name="retention_effective_prompt",
        group="retention",
        description="The whole Telegram retention prompt as assembled for this "
                    "product (its own Layer-1 core, the retention KB, the "
                    "Layer-3 block), plus the resolved retention variables.",
        method="GET", path="/admin/retention/effective-prompt",
        product="query!",
    ),
    Tool(
        name="retention_prompt_variables",
        group="retention",
        description="The Telegram persona's own variables — a SEPARATE registry "
                    "from the support one, with its own defaults and no "
                    "inheritance from support values.",
        method="GET", path="/admin/retention/prompt-variables",
        product="query!",
    ),
    Tool(
        name="set_retention_prompt_variables",
        group="retention",
        description="Replace the Telegram persona's variables for this product. "
                    "Send the whole map; empty values fall back to the retention "
                    "defaults. English only.",
        method="PUT", path="/admin/retention/prompt-variables",
        params=(Param("value", "object",
                      "Full {variable_key: text} map to store.",
                      required=True, location="body"),),
        product="query!", writes=True,
    ),

    # -------------------------------------------------------------- content
    Tool(
        name="kb_topics",
        group="content",
        description="The product's support topics (slug, canonical English "
                    "title, order, whether it has KB content).",
        method="GET", path="/admin/kb/topics",
        product="query",
    ),
    Tool(
        name="kb_content",
        group="content",
        description="The knowledge-base text of one topic — the Layer-2 block "
                    "the model answers from.",
        method="GET", path="/admin/kb/content",
        params=(Param("topic_id", "integer", "Topic id from `kb_topics`.",
                      required=True),),
    ),
    Tool(
        name="set_kb_content",
        group="content",
        description="Replace a topic's knowledge-base text. This is what the "
                    "support bot answers from, so a bad edit is visible to "
                    "players on the next message. English only.",
        method="PUT", path="/admin/kb/content",
        params=(
            Param("topic_id", "integer", "Topic id from `kb_topics`.",
                  required=True, location="body"),
            Param("content", "string", "The full replacement KB text.",
                  required=True, location="body"),
        ),
        writes=True,
    ),
    Tool(
        name="kb_variables",
        group="content",
        description="The {placeholder} registry substituted into the KB texts "
                    "(min deposit, payout window, …) with their current values.",
        method="GET", path="/admin/kb/variables",
        product="query",
    ),
    Tool(
        name="set_kb_variable",
        group="content",
        description="Set one KB {placeholder} value for this product. English only.",
        method="PUT", path="/admin/kb/variables/{key}",
        params=(
            Param("key", "string", "Registry key, e.g. min_deposit.",
                  required=True, location="path_body"),
            Param("value", "string", "The value substituted into KB texts.",
                  required=True, location="body"),
            Param("description", "string", "What this placeholder means.",
                  location="body"),
        ),
        product="query", writes=True,
    ),
    Tool(
        name="translations",
        group="content",
        description="The player-facing copy registry: every widget/bot string "
                    "per language, with the shipped default, the current "
                    "override and the resolved value.",
        method="GET", path="/admin/translations",
        product="query",
    ),
    Tool(
        name="set_translations",
        group="content",
        description="Replace this product's translation overrides. Send the "
                    "whole {lang: {key: text}} map — anything omitted falls "
                    "back to the shipped copy. Player-facing, so any language.",
        method="PUT", path="/admin/translations",
        params=(Param("value", "object",
                      "Full {lang: {key: text}} override map.",
                      required=True, location="body"),),
        product="query", writes=True,
    ),
    Tool(
        name="site_map",
        group="content",
        description="The official site pages both bots are allowed to link to "
                    "({title, url, purpose}).",
        method="GET", path="/admin/site-map",
        product="query",
    ),
    Tool(
        name="set_site_map",
        group="content",
        description="Replace the product's site map (the list of pages the bots "
                    "may link to). Send the whole list; each row needs an "
                    "http(s) url.",
        method="PUT", path="/admin/site-map",
        params=(Param("value", "array",
                      "Full list of {title, url, purpose} rows.",
                      required=True, location="body"),),
        product="query", writes=True,
    ),
    Tool(
        name="retention_kb",
        group="retention",
        description="The retention bot's knowledge base — one free-text "
                    "document per product.",
        method="GET", path="/admin/retention/kb/text",
        product="query!",
    ),
    Tool(
        name="set_retention_kb",
        group="retention",
        description="Replace the retention bot's whole knowledge-base document. "
                    "English only.",
        method="PUT", path="/admin/retention/kb/text",
        params=(Param("text", "string", "The full replacement document.",
                      required=True, location="body"),),
        product="query!", writes=True,
    ),

    # ------------------------------------------------------------- settings
    Tool(
        name="settings",
        group="settings",
        description="The hot-reloaded runtime knobs: the resolved (effective) "
                    "values plus the raw overrides of the layer being edited. "
                    "Groups: general, antispam, model, language, escalation, "
                    "retention.",
        method="GET", path="/admin/settings",
        product="query",
        note="Without a product_id it reads the deploy-wide global layer, which "
             "needs a global-scope key.",
    ),
    Tool(
        name="set_setting",
        group="settings",
        description="Write one settings group. Send the WHOLE group object — it "
                    "replaces the stored layer. With a product_id it writes that "
                    "product's layer, without one the global default layer.",
        method="PUT", path="/admin/settings/{key}",
        params=(
            Param("key", "string", "Group name.", required=True, location="path",
                  enum=("general", "antispam", "model", "language",
                        "escalation", "retention")),
            Param("value", "object", "The full group object to store.",
                  required=True, location="body"),
        ),
        product="query", writes=True,
        note="Read the group with `settings` first and send it back modified — "
             "a partial object drops the fields you omit.",
    ),

    # ------------------------------------------------------------ retention
    Tool(
        name="retention_agent_status",
        group="retention",
        description="The proactive agent's control panel in one call: enabled / "
                    "dry-run switches, today's spend vs budget, queue depth, "
                    "worker liveness, the effective guard values and which "
                    "events wake the agent.",
        method="GET", path="/admin/retention/v2/status",
        product="query!",
    ),
    Tool(
        name="retention_decisions",
        group="retention",
        description="The agent's decision ledger — one row per decision "
                    "(including silences): player state, guard verdict and "
                    "reasons, the chosen action, delivery result and cost.",
        method="GET", path="/admin/retention/v2/decisions",
        params=(_PAGE, _PAGE_SIZE),
        product="query!", redactable=True,
    ),
    Tool(
        name="retention_agent_logs",
        group="retention",
        description="The agent's durable system log (decisions, guard blocks, "
                    "failed sends, simulator injections, manual runs).",
        method="GET", path="/admin/retention/v2/logs",
        params=(_PAGE, _PAGE_SIZE),
        product="query!",
    ),
    Tool(
        name="retention_overview",
        group="retention",
        description="Telegram retention analytics: the lifetime player base, "
                    "range activity (actives, messages, photos, handoffs, pings "
                    "and their reply rate), the stage distribution and the AI "
                    "cost split.",
        method="GET", path="/admin/retention/overview",
        params=(_FROM, _TO),
        product="query",
    ),
    Tool(
        name="retention_sessions",
        group="retention",
        description="Telegram conversations for a product (kept apart from the "
                    "support chat list). Read a transcript with "
                    "`session_transcript`.",
        method="GET", path="/admin/retention/sessions",
        params=(_PAGE, _PAGE_SIZE),
        product="query!", redactable=True,
    ),

    # ------------------------------------------------------------------ raw
    Tool(
        name="admin_get",
        group="raw",
        description="Escape hatch: GET any other /admin/* endpoint that has no "
                    "dedicated tool (retention media, idle rules, funnel, "
                    "managers, structure details…). Read-only — writes are "
                    "deliberately limited to the named tools above.",
        method="GET", path="/admin/… (from the `path` argument)",
        params=(
            Param("path", "string",
                  "Path under /admin, e.g. '/admin/retention/idle/rules' or "
                  "'/admin/timeseries'.",
                  required=True, location="local"),
            Param("params", "object",
                  "Query parameters as a flat object, e.g. "
                  "{\"product_id\": 2, \"from\": \"2026-07-01\"}.",
                  location="local"),
        ),
        raw=True,
    ),
)

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def enabled_tools(allow_writes: bool = True) -> tuple:
    """The tools a server instance exposes.

    With writes off, the write tools are not merely refused at call time — they
    are never listed, so the model does not spend context on capabilities it
    cannot use (and cannot decide to try them).
    """
    if allow_writes:
        return TOOLS
    return tuple(t for t in TOOLS if not t.writes)


def manifest(allow_writes: bool = True) -> dict:
    """Serializable catalogue — rendered by the admin System → MCP page."""
    tools = enabled_tools(allow_writes)
    return {
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "groups": [{"key": k, "label": label} for k, label in GROUPS],
        "tools": [
            {
                "name": t.name,
                "group": t.group,
                "description": t.description,
                "note": t.note,
                "method": t.method,
                "path": t.path,
                "writes": t.writes,
                "product_scoped": bool(t.product),
                "product_required": t.product == "query!",
            }
            for t in tools
        ],
        "counts": {
            "total": len(tools),
            "read": sum(1 for t in tools if not t.writes),
            "write": sum(1 for t in tools if t.writes),
        },
    }
