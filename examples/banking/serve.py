"""Synthetic issuer/STS, A2A responder, and MCP servers for the banking examples.

These bounded fixtures demonstrate request/response and token boundaries. They
are not a production identity provider or complete MCP/A2A implementations.
"""
import base64
import hmac
import json
import os
import ssl
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt

from common import (ISSUER, GATEWAY, ACCESS_TOKEN, EXCHANGE_GRANT, AGENT_ID, EXCHANGE_ID,
                    POLICIES, ACCOUNT, TRANSACTIONS, issue, public_key, verify, receipt)


def agent_card():
    return {"name": "Synthetic transaction-review agent",
            "description": "Summarizes a synthetic transaction; no payments or financial decisions.",
            "version": "1.0.0", "supportedInterfaces": [{"url": POLICIES["review"]["audience"],
                "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
            "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer", "bearerFormat": "JWT"}}},
            "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
            "skills": [{"id": "transaction-summary", "name": "Synthetic transaction summary",
                "description": "Returns a deterministic demonstration summary.", "tags": ["banking", "synthetic"]}]}


def handler(kind, directory):
    directory = Path(directory)
    key = public_key(directory)
    signing_key = (directory / "signing.key").read_bytes() if kind == "issuer" else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # Never log tokens, credentials, or banking request bodies.

        def send(self, status, body=None, headers=None):
            payload = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items(): self.send_header(name, value)
            self.end_headers(); self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/healthz": return self.send(200, {"status": "ok"})
            if kind == "issuer":
                if self.path == "/jwks": return self.send(200, json.loads((directory / "jwks.json").read_text()))
                if self.path == "/.well-known/oauth-authorization-server":
                    return self.send(200, {"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks",
                        "token_endpoint": ISSUER + "/token", "response_types_supported": [],
                        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                        "grant_types_supported": ["client_credentials", EXCHANGE_GRANT]})
            if kind == "review" and self.path == "/.well-known/agent-card.json": return self.send(200, agent_card())
            return self.send(404, {"error": "Not found"})

        def read_body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536: raise ValueError("Invalid body length")
            return self.rfile.read(length)

        def client_auth(self, client, filename):
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "): raise PermissionError()
            try:
                user, password = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
                user, password = urllib.parse.unquote_plus(user), urllib.parse.unquote_plus(password)
            except (ValueError, UnicodeError): raise PermissionError() from None
            if user != client or not hmac.compare_digest(password, (directory / filename).read_text()):
                raise PermissionError()

        def token(self, raw):
            if self.path != "/token": return self.send(404, {"error": "Not found"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                return self.send(415, {"error": "Use form encoding"})
            values = urllib.parse.parse_qs(raw.decode(), strict_parsing=True)
            if any(len(value) != 1 for value in values.values()): raise ValueError("Repeated parameter")
            form = {name: value[0] for name, value in values.items()}
            grant = form.get("grant_type")
            if grant == "client_credentials":
                self.client_auth(AGENT_ID, "agent-secret")
                policy = next((p for p in POLICIES.values() if p["audience"] == form.get("resource")), None)
                if not policy or form.get("scope") != policy["gateway_scope"]:
                    return self.send(400, {"error": "invalid_target"})
                token = issue(signing_key, policy["audience"], policy["gateway_scope"])
                return self.send(200, {"access_token": token, "token_type": "Bearer", "expires_in": 300,
                                       "scope": policy["gateway_scope"]})
            if grant == EXCHANGE_GRANT:
                self.client_auth(EXCHANGE_ID, "exchange-secret")
                policy = next((p for p in POLICIES.values() if p["resource"] == form.get("resource")), None)
                if (not policy or form.get("scope") != policy["backend_scope"]
                        or form.get("subject_token_type") != ACCESS_TOKEN
                        or form.get("requested_token_type") != ACCESS_TOKEN):
                    return self.send(400, {"error": "invalid_target"})
                try:
                    claims = verify(form.get("subject_token", ""), key, policy["audience"], policy["gateway_scope"], "gateway")
                    if claims["sub"] != AGENT_ID: raise ValueError("Unapproved subject")
                except (jwt.PyJWTError, ValueError): return self.send(400, {"error": "invalid_grant"})
                token = issue(signing_key, policy["resource"], policy["backend_scope"], claims["sub"], "upstream", claims["exp"])
                return self.send(200, {"access_token": token, "token_type": "Bearer", "issued_token_type": ACCESS_TOKEN,
                                       "scope": policy["backend_scope"]})
            return self.send(400, {"error": "unsupported_grant_type"})

        def backend_auth(self):
            policy = POLICIES[kind]
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "): raise PermissionError()
            try:
                claims = verify(header[7:], key, policy["resource"], policy["backend_scope"], "upstream")
                if claims["sub"] != AGENT_ID or claims.get("act", {}).get("sub") != EXCHANGE_ID:
                    raise ValueError("Unapproved caller or actor")
                return claims
            except (jwt.PyJWTError, ValueError): raise PermissionError() from None

        def rpc(self, message, claims):
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0": raise ValueError("Invalid RPC")
            method, params = message.get("method"), message.get("params", {})
            if not isinstance(params, dict): raise ValueError("Invalid params")
            def result(value): return self.send(200, {"jsonrpc": "2.0", "id": message.get("id"), "result": value})
            def error(code, text): return self.send(200, {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": code, "message": text}})
            if kind == "review":
                if self.headers.get("A2A-Version") != "1.0": return error(-32009, "Use A2A 1.0")
                if method != "SendMessage": return error(-32601, "Only SendMessage is demonstrated")
                incoming = params.get("message", {})
                if not isinstance(incoming, dict) or incoming.get("role") != "ROLE_USER" or not incoming.get("messageId"):
                    raise ValueError("Invalid A2A message")
                parts = incoming.get("parts", [])
                if not isinstance(parts, list) or not parts or any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in parts):
                    raise ValueError("Use text parts")
                text = "Synthetic review of DEMO-TX-003: INR 1800.00 merchant purchase. Demonstration only; no action taken."
                return result({"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_AGENT",
                    "parts": [{"text": text}], "metadata": {"verified_upstream_identity": receipt(claims)}}})
            if method == "initialize":
                if params.get("protocolVersion") != "2025-11-25": raise ValueError("Use MCP 2025-11-25")
                return result({"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                               "serverInfo": {"name": "synthetic-bank-" + kind, "version": "1.0.0"}})
            if method == "notifications/initialized": return self.send(202)
            tool = "get_account_summary" if kind == "accounts" else "list_recent_transactions"
            if method == "tools/list":
                return result({"tools": [{"name": tool, "description": "Read-only synthetic banking data",
                    "inputSchema": {"type": "object", "properties": {"account_id": {"type": "string", "enum": ["DEMO-001"]}},
                                    "required": ["account_id"], "additionalProperties": False}}]})
            if method == "tools/call":
                if params.get("name") != tool: return error(-32602, "Unknown tool")
                if params.get("arguments", {}).get("account_id") != "DEMO-001":
                    return result({"content": [{"type": "text", "text": "Only synthetic account DEMO-001 is accessible"}], "isError": True})
                data = dict(ACCOUNT) if kind == "accounts" else {"account_id": "DEMO-001", "transactions": TRANSACTIONS, "synthetic": True}
                data["verified_upstream_identity"] = receipt(claims)
                return result({"content": [{"type": "text", "text": json.dumps(data)}], "isError": False})
            return error(-32601, "Method not demonstrated")

        def do_POST(self):
            try:
                raw = self.read_body()
                if kind == "issuer": return self.token(raw)
                if self.path != ("/rpc" if kind == "review" else "/mcp"):
                    return self.send(404, {"error": "Not found"})
                claims = self.backend_auth()
                return self.rpc(json.loads(raw), claims)
            except PermissionError: return self.send(401, {"error": "Invalid credentials"}, {"WWW-Authenticate": "Bearer" if kind != "issuer" else "Basic"})
            except (ValueError, TypeError, KeyError): return self.send(400, {"error": "Invalid demo request"})
    return Handler


def main():
    kind = os.environ["DEMO_SERVICE"]
    directory = os.environ.get("DEMO_CREDENTIALS", "/credentials")
    port = 8443 if kind == "issuer" else 8080
    server = ThreadingHTTPServer(("0.0.0.0", port), handler(kind, directory))
    if kind == "issuer":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(Path(directory) / "tls.pem"), str(Path(directory) / "tls.key"))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__": main()
