"""MCP server exposing the support-chat admin API to an AI agent.

A standalone CLIENT of the deployed service — it speaks MCP on stdio and plain
HTTPS to `/admin/*` with a service API key (`sak_…`). It imports nothing from
the service itself (no db, no config, no settings), so it can be pointed at any
deployment, and every authorization rule stays where it belongs: at the API
choke points in `app/api/admin_auth.py`.

Run it with `python -m mcp_server`; see `.mcp.json` at the repo root and the
admin panel's System -> MCP page for setup.
"""
from mcp_server.catalog import SERVER_NAME, SERVER_VERSION

__all__ = ["SERVER_NAME", "SERVER_VERSION"]
