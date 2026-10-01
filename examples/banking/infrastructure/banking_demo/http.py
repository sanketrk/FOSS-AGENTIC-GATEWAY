"""Small HTTP adapters shared by the standalone banking examples."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from foss_agentic_gateway import AuthenticationError


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send(self, status, body=None, headers=None):
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items(): self.send_header(name, value)
        self.end_headers(); self.wfile.write(payload)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 65536: raise ValueError("Invalid body length")
        return self.rfile.read(length)

    def do_GET(self):
        if self.path == "/healthz": return self.send(200, {"status": "ok"})
        return self.send(404, {"error": "Not found"})

    def result(self, value):
        return self.send(200, {"jsonrpc": "2.0", "id": self.request_id, "result": value})

    def rpc_error(self, code, text):
        return self.send(200, {"jsonrpc": "2.0", "id": self.request_id,
                               "error": {"code": code, "message": text}})


def backend_handler(verifier, dispatch, *, rpc_path, card=None):
    class Handler(DemoHandler):
        def do_GET(self):
            if card and self.path == "/.well-known/agent-card.json": return self.send(200, card())
            return super().do_GET()

        def do_POST(self):
            if self.path != rpc_path: return self.send(404, {"error": "Not found"})
            try:
                # SDK authorization runs before any application method is invoked.
                claims = verifier.verify(self.headers.get("Authorization"))
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.send(415, {"error": "Use application/json"})
                message = json.loads(self.read_body())
                if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                        or not isinstance(message.get("method"), str) or not isinstance(message.get("params", {}), dict)):
                    raise ValueError("Invalid RPC")
                self.request_id = message.get("id")
                return dispatch(self, message, claims)
            except AuthenticationError:
                return self.send(401, {"error": "Exchanged gateway token required"}, {"WWW-Authenticate": "Bearer"})
            except (ValueError, TypeError, KeyError):
                return self.send(400, {"error": "Invalid demo request"})
    return Handler


def run(handler):
    ThreadingHTTPServer(("0.0.0.0", 8080), handler).serve_forever()


def receipt(claims):
    # Diagnostic output only; never used for authorization.
    return {"subject": claims["sub"], "audience": claims["aud"], "scope": claims["scope"],
            "actor": claims["act"]["sub"], "token_use": claims.get("token_use")}
