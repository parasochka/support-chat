"""MCP server: protocol handshake, tool dispatch, redaction and write gating.

The server is a pure dict-in/dict-out dispatcher over a swappable HTTP client,
so every case here runs in-process — no subprocess, no network, no DB.
"""
from __future__ import annotations

import io
import json

import pytest

from mcp_server import catalog
from mcp_server.client import AdminClient, ToolError
from mcp_server.server import MCPServer, serve


class RecordingClient(AdminClient):
    """Stands in for the deployed service: records calls, returns canned rows."""

    def __init__(self, payload=None, default_product_id=None, fail=None):
        super().__init__("https://svc.example", "sak_test",
                         default_product_id=default_product_id)
        self.calls = []
        self.payload = payload if payload is not None else {"ok": True}
        self.fail = fail

    def request(self, method, path, *, params=None, body=None):
        self.calls.append({"method": method, "path": path,
                           "params": params or {}, "body": body})
        if self.fail:
            raise ToolError(self.fail)
        return self.payload

    def close(self):
        pass


def rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method,
                          "params": params or {}})


def call(server, name, args=None, mid=1):
    return rpc(server, "tools/call", {"name": name, "arguments": args or {}}, mid)


def text_of(response):
    return response["result"]["content"][0]["text"]


# --------------------------------------------------------------------------
# protocol
# --------------------------------------------------------------------------
def test_initialize_echoes_a_supported_protocol_version():
    srv = MCPServer(client=RecordingClient())
    res = rpc(srv, "initialize", {"protocolVersion": "2024-11-05"})["result"]
    assert res["protocolVersion"] == "2024-11-05"
    assert res["capabilities"]["tools"] is not None
    assert res["serverInfo"]["name"] == catalog.SERVER_NAME


def test_initialize_falls_back_to_our_newest_version_for_an_unknown_one():
    from mcp_server.server import PROTOCOL_VERSIONS
    srv = MCPServer(client=RecordingClient())
    res = rpc(srv, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert res["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_notifications_get_no_response():
    srv = MCPServer(client=RecordingClient())
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    # Some clients wrongly stamp an id on a notification — still no response.
    assert srv.handle({"jsonrpc": "2.0", "id": 9,
                       "method": "notifications/cancelled"}) is None


def test_unknown_method_is_a_jsonrpc_error():
    srv = MCPServer(client=RecordingClient())
    assert rpc(srv, "does/not/exist")["error"]["code"] == -32601


def test_tools_list_schemas_are_wellformed():
    srv = MCPServer(client=RecordingClient())
    tools = rpc(srv, "tools/list")["result"]["tools"]
    assert len(tools) == len(catalog.TOOLS)
    names = set()
    for tool in tools:
        assert tool["name"] not in names, "duplicate tool name"
        names.add(tool["name"])
        assert tool["description"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        for spec in schema["properties"].values():
            assert spec["type"] in ("string", "integer", "boolean",
                                    "object", "array")
        # Every declared-required property must exist in the schema.
        for req in schema.get("required", []):
            assert req in schema["properties"]


def test_a_bad_frame_does_not_kill_the_loop():
    srv = MCPServer(client=RecordingClient())
    stdin = io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    stdout = io.StringIO()
    serve(srv, stdin=stdin, stdout=stdout, stderr=io.StringIO())
    frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert frames[0]["error"]["code"] == -32700
    assert frames[1]["result"] == {}


# --------------------------------------------------------------------------
# request building
# --------------------------------------------------------------------------
def test_query_path_and_body_params_land_where_declared():
    client = RecordingClient()
    srv = MCPServer(client=client)
    call(srv, "set_kb_variable", {"key": "min_deposit", "value": "10 USD",
                                  "description": "smallest top-up",
                                  "product_id": 7})
    sent = client.calls[-1]
    assert sent["method"] == "PUT"
    assert sent["path"] == "/admin/kb/variables/min_deposit"
    assert sent["params"] == {"product_id": 7}
    # `key` is declared path_body: the endpoint cross-checks the two.
    assert sent["body"] == {"key": "min_deposit", "value": "10 USD",
                            "description": "smallest top-up"}


def test_product_id_defaults_to_the_configured_product():
    client = RecordingClient(default_product_id=3)
    srv = MCPServer(client=client)
    call(srv, "effective_prompt", {})
    assert client.calls[-1]["params"]["product_id"] == 3
    # An explicit argument still wins over the default.
    call(srv, "effective_prompt", {"product_id": 8})
    assert client.calls[-1]["params"]["product_id"] == 8


def test_a_product_required_tool_explains_itself_instead_of_422ing():
    srv = MCPServer(client=RecordingClient())
    res = call(srv, "retention_agent_status", {})["result"]
    assert res["isError"] is True
    assert "product_id" in res["content"][0]["text"]


def test_missing_required_argument_is_reported_as_a_tool_error():
    client = RecordingClient()
    srv = MCPServer(client=client)
    res = call(srv, "kb_content", {})["result"]
    assert res["isError"] is True
    assert "topic_id" in res["content"][0]["text"]
    assert client.calls == []


def test_unknown_tool_is_a_tool_error_not_a_protocol_error():
    srv = MCPServer(client=RecordingClient())
    res = call(srv, "rm_rf")["result"]
    assert res["isError"] is True


def test_api_failure_surfaces_the_servers_explanation():
    srv = MCPServer(client=RecordingClient(fail="HTTP 403 …: forbidden."))
    res = call(srv, "logs", {"level": "ERROR"})["result"]
    assert res["isError"] is True
    assert "403" in res["content"][0]["text"]


# --------------------------------------------------------------------------
# the escape hatch stays inside /admin
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/chat/topics", "/healthz",
                                  "/adminx/secret", "//evil.example/admin"])
def test_admin_get_refuses_paths_outside_the_admin_surface(path):
    client = RecordingClient()
    srv = MCPServer(client=client)
    res = call(srv, "admin_get", {"path": path})["result"]
    assert res["isError"] is True
    assert client.calls == []


def test_admin_get_passes_query_params_through():
    client = RecordingClient()
    srv = MCPServer(client=client)
    call(srv, "admin_get", {"path": "/admin/timeseries",
                            "params": {"product_id": 2, "from": "2026-07-01"}})
    assert client.calls[-1] == {"method": "GET", "path": "/admin/timeseries",
                                "params": {"product_id": 2,
                                           "from": "2026-07-01"},
                                "body": None}


def test_admin_get_is_read_only_by_construction():
    assert catalog.TOOLS_BY_NAME["admin_get"].method == "GET"
    assert not catalog.TOOLS_BY_NAME["admin_get"].writes


# --------------------------------------------------------------------------
# write gating
# --------------------------------------------------------------------------
def test_read_only_server_hides_the_write_tools_entirely():
    client = RecordingClient()
    srv = MCPServer(client=client, allow_writes=False)
    listed = {t["name"] for t in rpc(srv, "tools/list")["result"]["tools"]}
    assert "set_kb_content" not in listed
    assert "kb_content" in listed
    res = call(srv, "set_kb_content", {"topic_id": 1, "content": "x"})["result"]
    assert res["isError"] is True
    # And the call never left the process.
    assert client.calls == []


def test_no_write_tool_touches_a_destructive_endpoint():
    """Deleting users/products/sessions/media and rotating keys stay out of the
    agent's reach by construction — the catalogue is the whole surface."""
    for tool in catalog.TOOLS:
        assert tool.method != "DELETE"
        assert "/users" not in tool.path
        assert "/api-keys" not in tool.path
        assert "/secrets" not in tool.path
        assert "widget-key" not in tool.path


# --------------------------------------------------------------------------
# PII redaction
# --------------------------------------------------------------------------
def test_player_identity_is_masked_by_default_but_messages_are_not():
    payload = {"items": [{"session_id": "s1", "full_name": "Ivan Petrov",
                          "email": "ivan@example.com",
                          "first_message": "where is my withdrawal?"}]}
    srv = MCPServer(client=RecordingClient(payload))
    out = json.loads(text_of(call(srv, "sessions", {"product_id": 1})))
    row = out["items"][0]
    assert row["full_name"] == "***"
    assert row["email"] == "***"
    assert row["first_message"] == "where is my withdrawal?"


def test_redaction_can_be_turned_off_per_call():
    payload = {"messages": [{"full_name": "Ivan"}]}
    srv = MCPServer(client=RecordingClient(payload))
    out = json.loads(text_of(call(srv, "session_transcript",
                                  {"session_id": "abc", "redact_pii": False})))
    assert out["messages"][0]["full_name"] == "Ivan"


def test_non_redactable_tools_are_left_alone():
    """The prompt legitimately contains the sample player's name — masking it
    would hide what the model actually receives."""
    payload = {"effective_preview": {"user": "PLAYER: full_name=Ivan"}}
    srv = MCPServer(client=RecordingClient(payload))
    assert "Ivan" in text_of(call(srv, "effective_prompt", {"product_id": 1}))


# --------------------------------------------------------------------------
# response size
# --------------------------------------------------------------------------
def test_a_huge_payload_is_truncated_with_an_explanation():
    payload = {"items": [{"text": "x" * 200} for _ in range(200)]}
    srv = MCPServer(client=RecordingClient(payload), max_response_chars=2000)
    out = text_of(call(srv, "logs", {}))
    assert len(out) < 2400
    assert "truncated" in out


# --------------------------------------------------------------------------
# the admin panel renders this same catalogue
# --------------------------------------------------------------------------
def test_manifest_matches_the_served_tools():
    served = {t["name"] for t in
              MCPServer(client=RecordingClient()).handle(
                  {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
              )["result"]["tools"]}
    assert {t["name"] for t in catalog.manifest()["tools"]} == served
    counts = catalog.manifest()["counts"]
    assert counts["read"] + counts["write"] == counts["total"]
    # Every tool sits in a group the admin page knows how to render.
    known = {k for k, _ in catalog.GROUPS}
    assert all(t.group in known for t in catalog.TOOLS)


def test_the_admin_manifest_endpoint_serves_the_same_catalogue():
    """The System → MCP page renders from this, so it must not drift."""
    from app.api import admin as admin_api
    paths = [r.path for r in admin_api.router.routes]
    assert "/admin/mcp/manifest" in paths
    payload = catalog.manifest(allow_writes=True)
    assert payload["tools"] and payload["counts"]["write"] > 0
    assert {g["key"] for g in payload["groups"]} == {k for k, _ in catalog.GROUPS}


def test_unconfigured_server_answers_with_the_missing_variables():
    srv = MCPServer(client=AdminClient())
    res = call(srv, "structure")["result"]
    assert res["isError"] is True
    assert "SUPPORT_ADMIN_URL" in res["content"][0]["text"]
    assert "SUPPORT_ADMIN_KEY" in res["content"][0]["text"]
