"""HTTP client for the deployed admin API — the MCP server's only I/O.

Deliberately thin: it attaches the service key, sends the request, and turns a
failure into a message an agent can act on. It holds no authorization logic of
its own — the `sak_…` key's role × scope is enforced server-side by
`require_admin`, so the worst a misbehaving client can do is get a 403.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

# The env vars a user sets in .mcp.json. Kept here (not in config.py) because
# this package is a standalone client: it never imports the service.
ENV_URL = "SUPPORT_ADMIN_URL"
ENV_KEY = "SUPPORT_ADMIN_KEY"
ENV_PRODUCT = "SUPPORT_ADMIN_PRODUCT_ID"
ENV_ALLOW_WRITES = "SUPPORT_ADMIN_ALLOW_WRITES"
ENV_TIMEOUT = "SUPPORT_ADMIN_TIMEOUT_SEC"
ENV_MAX_CHARS = "SUPPORT_ADMIN_MAX_RESPONSE_CHARS"
ENV_REDACT = "SUPPORT_ADMIN_REDACT_PII"


class ToolError(Exception):
    """A failure to report inside the tool result (never a transport error)."""


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


class AdminClient:
    """Calls `/admin/*` on the deployed service with a service API key."""

    def __init__(self, base_url: str = "", token: str = "",
                 timeout: float = 30.0,
                 default_product_id: Optional[int] = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self.default_product_id = default_product_id
        self._client: Optional[httpx.Client] = None

    # -- configuration ------------------------------------------------------
    @classmethod
    def from_env(cls) -> "AdminClient":
        product = os.getenv(ENV_PRODUCT, "").strip()
        try:
            product_id = int(product) if product else None
        except ValueError:
            product_id = None
        return cls(
            base_url=os.getenv(ENV_URL, "").strip(),
            token=os.getenv(ENV_KEY, "").strip(),
            timeout=float(env_int(ENV_TIMEOUT, 30)),
            default_product_id=product_id,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def config_error(self) -> str:
        missing = []
        if not self.base_url:
            missing.append(ENV_URL)
        if not self.token:
            missing.append(ENV_KEY)
        return (
            "The MCP server is not configured: set "
            + " and ".join(missing)
            + ". Mint a service key in the admin panel under System -> MCP "
              "(or System -> API keys) and put it in the server's env."
        )

    # -- transport ----------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.token}",
                         "Accept": "application/json",
                         "User-Agent": "support-chat-mcp/1.0"},
                follow_redirects=True,
            )
        return self._client

    def request(self, method: str, path: str, *,
                params: Optional[dict] = None,
                body: Optional[dict] = None) -> Any:
        if not self.configured:
            raise ToolError(self.config_error())
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        clean = {k: v for k, v in (params or {}).items()
                 if v is not None and v != ""}
        # Booleans have to go over the wire as the lowercase literals FastAPI
        # parses; httpx would otherwise send Python's "True".
        for k, v in list(clean.items()):
            if isinstance(v, bool):
                clean[k] = "true" if v else "false"
        try:
            resp = self._http().request(method, url, params=clean, json=body)
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ToolError(self._explain(resp))
        if not resp.content:
            return {"ok": True}
        try:
            return resp.json()
        except ValueError:
            return {"text": resp.text}

    @staticmethod
    def _explain(resp: httpx.Response) -> str:
        """Turn an API error into something the agent can act on rather than a
        bare status code — the scope errors in particular are actionable
        ("use a global key", "this key cannot write here")."""
        detail = ""
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or payload.get("message") or "")
            if not detail:
                detail = json.dumps(payload)[:400]
        except ValueError:
            detail = (resp.text or "")[:400]
        code = resp.status_code
        hint = ""
        if code == 401:
            hint = (" The service key was rejected — check SUPPORT_ADMIN_KEY, "
                    "and that the key is still active in System -> API keys.")
        elif code == 403:
            hint = (" The key's role x scope does not cover this call: a "
                    "manager key cannot write, and a product-scoped key cannot "
                    "read deploy-wide data such as the system logs.")
        elif code == 404:
            hint = " Check the path and ids (see the `structure` tool)."
        elif code == 422:
            hint = " A required argument is missing or malformed."
        return f"HTTP {code} from {resp.request.url.path}: {detail}.{hint}".strip()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
