"""Standalone read-only banking MCP server with mandatory SDK exchange-token verification."""
import json
import os
from pathlib import Path

from foss_agentic_gateway import ExchangeTokenVerifier
from banking_demo.config import ISSUER, AGENT_ID, EXCHANGE_ID, POLICIES
from banking_demo.http import backend_handler, receipt, run

ACCOUNT = {"account_id": "DEMO-001", "currency": "INR", "available_balance": "125000.00",
           "status": "active", "synthetic": True}

POLICY = POLICIES["accounts"]
TOOL = "get_account_summary"


def dispatch(request, message, claims):
    method, params = message["method"], message.get("params", {})
    if method == "initialize":
        if params.get("protocolVersion") != "2025-11-25": raise ValueError("Use MCP 2025-11-25")
        return request.result({"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "synthetic-bank-accounts", "version": "1.0.0"}})
    if method == "notifications/initialized": return request.send(202)
    if method == "tools/list":
        return request.result({"tools": [{"name": TOOL, "description": "Read-only synthetic banking data",
            "inputSchema": {"type": "object", "properties": {"account_id": {"type": "string", "enum": ["DEMO-001"]}},
                            "required": ["account_id"], "additionalProperties": False}}]})
    if method == "tools/call":
        if params.get("name") != TOOL: return request.rpc_error(-32602, "Unknown tool")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict) or arguments.get("account_id") != "DEMO-001":
            return request.result({"content": [{"type": "text", "text": "Only synthetic account DEMO-001 is accessible"}], "isError": True})
        data = dict(ACCOUNT)
        data["verified_upstream_identity"] = receipt(claims)
        return request.result({"content": [{"type": "text", "text": json.dumps(data)}], "isError": False})
    return request.rpc_error(-32601, "Method not demonstrated")


def handler(credentials):
    verifier = ExchangeTokenVerifier(issuer=ISSUER, audience=POLICY["resource"], scopes=(POLICY["backend_scope"],),
        gateway_actor=EXCHANGE_ID, subjects=(AGENT_ID,), jwks=json.loads((Path(credentials) / "jwks.json").read_text()))
    return backend_handler(verifier, dispatch, rpc_path="/mcp")


if __name__ == "__main__": run(handler(os.environ.get("DEMO_CREDENTIALS", "/credentials")))
