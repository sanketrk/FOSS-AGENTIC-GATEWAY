"""Standalone A2A transaction-review agent, requiring SDK-verified exchanged tokens."""
import json
import os
import uuid
from pathlib import Path

from foss_agentic_gateway import ExchangeTokenVerifier
from banking_demo.config import ISSUER, AGENT_ID, EXCHANGE_ID, POLICIES
from banking_demo.http import backend_handler, receipt, run

POLICY = POLICIES["review"]


def agent_card():
    return {"name": "Synthetic transaction-review agent",
            "description": "Summarizes a synthetic transaction; no payments or financial decisions.",
            "version": "1.0.0", "supportedInterfaces": [{"url": POLICY["audience"],
                "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
                {"url": POLICY["audience"] + "/rest", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
            "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer", "bearerFormat": "JWT"}}},
            "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
            "skills": [{"id": "transaction-summary", "name": "Synthetic transaction summary",
                "description": "Returns a deterministic demonstration summary.", "tags": ["banking", "synthetic"]}]}


def dispatch(request, message, claims):
    if request.headers.get("A2A-Version") != "1.0": return request.rpc_error(-32009, "Use A2A 1.0")
    if message["method"] != "SendMessage": return request.rpc_error(-32601, "Only SendMessage is demonstrated")
    incoming = message.get("params", {}).get("message", {})
    if not isinstance(incoming, dict) or incoming.get("role") != "ROLE_USER" or not incoming.get("messageId"):
        raise ValueError("Invalid A2A message")
    parts = incoming.get("parts", [])
    if not isinstance(parts, list) or not parts or any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in parts):
        raise ValueError("Use text parts")
    text = "Synthetic review of DEMO-TX-003: INR 1800.00 merchant purchase. Demonstration only; no action taken."
    result = {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_AGENT", "parts": [{"text": text}],
                                      "metadata": {"verified_upstream_identity": receipt(claims)}}}
    return request.send(200, result) if getattr(request, "rest_binding", False) else request.result(result)


def rest_dispatch(request, message, claims):
    request.rest_binding = True
    request.request_id = None
    if request.headers.get("A2A-Version") != "1.0":
        return request.send(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Use A2A 1.0"}})
    return dispatch(request, {"method": "SendMessage", "params": message}, claims)


def handler(credentials):
    verifier = ExchangeTokenVerifier(issuer=ISSUER, audience=POLICY["resource"], scopes=(POLICY["backend_scope"],),
        gateway_actor=EXCHANGE_ID, subjects=(AGENT_ID,), jwks=json.loads((Path(credentials) / "jwks.json").read_text()))
    return backend_handler(verifier, dispatch, rpc_path="/rpc", card=agent_card, rest_dispatch=rest_dispatch)


if __name__ == "__main__": run(handler(os.environ.get("DEMO_CREDENTIALS", "/credentials")))
