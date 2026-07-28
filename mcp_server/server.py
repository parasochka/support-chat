"""The MCP server itself: newline-delimited JSON-RPC 2.0 over stdio.

Hand-rolled rather than built on the MCP SDK, for the same reason `auth.py`
hand-rolls JWT: the wire format is small and fully specified, and a stdlib
implementation keeps the repo installable with the deps it already has (httpx
is the only import outside the stdlib) — no extra runtime dependency, no build
step, and the whole protocol layer is a pure dict-in/dict-out function that
pytest can drive without a subprocess.

Protocol surface (MCP 2024-11-05 .. 2025-06-18, which are compatible for a
tools-only server):

    initialize                -> capabilities + serverInfo
    notifications/initialized -> (no response)
    ping                      -> {}
    tools/list                -> the catalogue
    tools/call                -> the endpoint result as JSON text

A tool FAILURE is reported inside the result (`isError: true`), not as a
JSON-RPC error: per spec the model is meant to see it and adapt. JSON-RPC
errors are reserved for protocol-level problems (bad method, malformed frame).

STDOUT IS THE PROTOCOL CHANNEL — nothing may print to it but frames. All
diagnostics go to stderr.
"""
from __future__ import annotations

import json
import posixpath
import sys
from typing import Any, Optional

from mcp_server import catalog
from mcp_server.client import (ENV_ALLOW_WRITES, ENV_MAX_CHARS, ENV_REDACT,
                               AdminClient, ToolError, env_flag, env_int)

# Newest first: we echo the client's version when we know it, else our newest.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# Keys whose values are masked before a payload reaches the model. Player
# identity, not content: the transcript text itself is the thing being
# debugged, so it is never touched.
PII_KEYS = frozenset({
    "email", "full_name", "first_name", "last_name", "name_first",
    "phone", "player_email", "player_name", "tg_username", "username",
    # Manager identities: /admin/managers returns `display_name` and the
    # sessions list joins it in as `manager_name` — the very fields the
    # admin_get redaction exists for.
    "display_name", "manager_name",
})
MASK = "***"


def redact(value: Any) -> Any:
    """Mask player-identifying fields anywhere in a JSON payload.

    Admin ACCOUNT emails (the audit trail's actors) are useful and not player
    data, but they live under the same `email` key, so this masks them too —
    the honest trade for a rule simple enough to be obviously correct. Pass
    redact_pii=false when the identity is what you are looking at.
    """
    if isinstance(value, dict):
        return {k: (MASK if (k in PII_KEYS and isinstance(v, str) and v)
                    else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class MCPServer:
    """Protocol dispatch + tool execution. No I/O of its own — `serve()` owns
    the stdio loop, so `handle()` stays unit-testable."""

    def __init__(self, client: Optional[AdminClient] = None, *,
                 allow_writes: bool = True,
                 max_response_chars: int = 40000,
                 redact_default: bool = True) -> None:
        self.client = client if client is not None else AdminClient.from_env()
        self.allow_writes = allow_writes
        self.max_response_chars = max_response_chars
        self.redact_default = redact_default
        self.tools = catalog.enabled_tools(allow_writes)
        self.tools_by_name = {t.name: t for t in self.tools}

    @classmethod
    def from_env(cls) -> "MCPServer":
        return cls(
            client=AdminClient.from_env(),
            allow_writes=env_flag(ENV_ALLOW_WRITES, True),
            max_response_chars=env_int(ENV_MAX_CHARS, 40000),
            redact_default=env_flag(ENV_REDACT, True),
        )

    # -- JSON-RPC -----------------------------------------------------------
    def handle(self, msg: dict) -> Optional[dict]:
        """One request in, one response out (None for notifications)."""
        mid = msg.get("id")
        method = msg.get("method")
        if method is None:
            return self._error(mid, -32600, "Not a JSON-RPC request.")
        if mid is None or method.startswith("notifications/"):
            # A notification: initialized / cancelled / progress. Nothing to
            # answer, and answering would be a protocol violation. Some clients
            # stamp an id on one anyway, so the method prefix decides too.
            return None
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                return self._ok(mid, self._initialize(params))
            if method == "ping":
                return self._ok(mid, {})
            if method == "tools/list":
                return self._ok(mid, {"tools": [self._tool_spec(t)
                                                for t in self.tools]})
            if method == "tools/call":
                return self._ok(mid, self._call(params))
        except Exception as exc:  # noqa: BLE001 - a crash must not kill the loop
            return self._error(mid, -32603, f"Internal error: {exc}")
        return self._error(mid, -32601, f"Unknown method: {method}")

    def _initialize(self, params: dict) -> dict:
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": catalog.SERVER_NAME,
                           "version": catalog.SERVER_VERSION},
            "instructions": self._instructions(),
        }

    def _instructions(self) -> str:
        target = self.client.base_url or "(unconfigured)"
        lines = [
            f"Admin API of the AI support-chat service at {target}.",
            "The service is multi-tenant: partners own products (casinos), and "
            "almost everything resolves per product — call `structure` first to "
            "get a product_id, then pass it to the other tools.",
        ]
        if self.client.default_product_id:
            lines.append(
                f"This server defaults to product_id={self.client.default_product_id} "
                "when a tool is called without one.")
        if self.allow_writes:
            lines.append(
                "Write tools change live behaviour for real players and are "
                "recorded in the admin audit trail under this key's name. Read "
                "the current value first and send back a full object — a "
                "partial write drops the fields you omit.")
        else:
            lines.append("This server is read-only.")
        return " ".join(lines)

    def _tool_spec(self, tool) -> dict:
        return {
            "name": tool.name,
            "description": tool.full_description(),
            "inputSchema": tool.input_schema(),
        }

    # -- tool execution -----------------------------------------------------
    def _call(self, params: dict) -> dict:
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        tool = self.tools_by_name.get(name)
        if tool is None:
            known = catalog.TOOLS_BY_NAME.get(name)
            if known is not None and known.writes:
                return self._tool_error(
                    f"Tool {name!r} is a write tool and this server runs "
                    f"read-only ({ENV_ALLOW_WRITES}=0).")
            return self._tool_error(f"Unknown tool: {name!r}.")
        try:
            payload = self._execute(tool, args)
        except ToolError as exc:
            return self._tool_error(str(exc))
        if args.get("redact_pii", self.redact_default) and tool.redactable:
            payload = redact(payload)
        return {"content": [{"type": "text", "text": self._render(payload)}],
                "isError": False}

    def _execute(self, tool, args: dict) -> Any:
        if tool.raw:
            return self._execute_raw(tool, args)
        path = tool.path
        query: dict = {}
        body: dict = {}
        for p in tool.all_params:
            if p.name not in args or args[p.name] is None:
                if p.required and p.name != "product_id":
                    raise ToolError(f"Missing required argument: {p.name!r}.")
                continue
            value = args[p.name]
            if p.location in ("path", "path_body"):
                path = path.replace("{" + p.name + "}", str(value))
            if p.location in ("body", "path_body"):
                body[p.name] = value
            elif p.location == "query":
                query[p.name] = value
        product_id = self._resolve_product(tool, args)
        if product_id is not None:
            if tool.product == "body":
                body["product_id"] = product_id
            else:
                query["product_id"] = product_id
        return self.client.request(tool.method, path, params=query,
                                   body=body or None)

    def _resolve_product(self, tool, args: dict) -> Optional[int]:
        """The acting product: explicit argument, else the server default.

        Falling back to the configured default is what makes single-product
        setups pleasant — but a tool whose endpoint REQUIRES a product must say
        so clearly rather than 422 from FastAPI.
        """
        if not tool.product:
            return None
        raw = args.get("product_id")
        if raw in (None, ""):
            raw = self.client.default_product_id
        if raw in (None, ""):
            if tool.product == "query!":
                raise ToolError(
                    f"{tool.name} needs a product_id (this endpoint is "
                    "per-product). Call `structure` to list the products this "
                    "key can reach, or set SUPPORT_ADMIN_PRODUCT_ID.")
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ToolError(f"product_id must be a number, got {raw!r}.")

    def _execute_raw(self, tool, args: dict) -> Any:
        path = str(args.get("path") or "").strip()
        # Reject a path that carries its own query/fragment or a scheme: those
        # would smuggle state past the `params` argument (and past the check
        # below, since only the part before "?" is compared).
        if any(c in path for c in "?#") or "//" in path or ":" in path:
            raise ToolError("`path` must be a bare /admin/... path — put query "
                            "parameters in `params`.")
        # No percent-escapes either: posixpath.normpath below doesn't decode
        # them, so "/admin/%2e%2e/api/..." would sail through the prefix check
        # encoded and be decoded only later (today's server routes it to a 404,
        # but a normalizing reverse proxy in front could collapse it — cheap to
        # refuse outright). Admin paths never need percent-encoding; anything
        # that does belongs in `params`, which httpx encodes itself.
        if "%" in path:
            raise ToolError("`path` must not contain percent-escapes — put "
                            "query parameters in `params`.")
        if not path.startswith("/"):
            path = "/" + path
        # NORMALIZE BEFORE CHECKING. httpx resolves dot segments when it builds
        # the URL, so a raw-string prefix test passed
        # "/admin/../api/chat/topics" while the request actually went to
        # /api/chat/topics — with the admin Bearer token attached. That is
        # exactly what this guard exists to prevent: the escape hatch stays
        # inside the admin surface and must never become a way to poke the
        # public chat API (or any other host path) with an admin credential.
        path = posixpath.normpath(path)
        if not (path == "/admin" or path.startswith("/admin/")):
            raise ToolError("admin_get only reaches paths under /admin/.")
        query = args.get("params") or {}
        if not isinstance(query, dict):
            raise ToolError("`params` must be an object of query parameters.")
        query = dict(query)
        if "product_id" not in query and self.client.default_product_id:
            query["product_id"] = self.client.default_product_id
        return self.client.request("GET", path, params=query)

    def _render(self, payload: Any) -> str:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(text) > self.max_response_chars:
            keep = self.max_response_chars
            text = (text[:keep] + f"\n\n… [truncated at {keep} characters of "
                    f"{len(text)}. Narrow the range, lower `limit`/`page_size`, "
                    f"or raise SUPPORT_ADMIN_MAX_RESPONSE_CHARS.]")
        return text

    # -- framing helpers ----------------------------------------------------
    @staticmethod
    def _ok(mid: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(message: str) -> dict:
        return {"content": [{"type": "text", "text": message}], "isError": True}


def serve(server: Optional[MCPServer] = None,
          stdin=None, stdout=None, stderr=None) -> None:
    """Read frames from stdin, write responses to stdout, until EOF."""
    srv = server if server is not None else MCPServer.from_env()
    src = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    if not srv.client.configured:
        # Start anyway: a server that exits here shows up in the client as an
        # opaque "failed to connect", whereas staying up lets every tool call
        # answer with the actual missing-variable message.
        print(srv.client.config_error(), file=err, flush=True)

    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out.write(json.dumps(MCPServer._error(
                None, -32700, "Parse error: not JSON.")) + "\n")
            out.flush()
            continue
        if isinstance(msg, list):
            # Batches were dropped in the 2025-06-18 revision; older clients
            # may still send one, so answer each frame in order.
            responses = [r for r in (srv.handle(m) for m in msg) if r]
            for response in responses:
                out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()
            continue
        if not isinstance(msg, dict):
            continue
        response = srv.handle(msg)
        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()
    srv.client.close()
