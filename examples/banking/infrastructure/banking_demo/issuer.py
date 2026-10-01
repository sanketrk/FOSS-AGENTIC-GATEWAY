"""Disposable OAuth issuer/STS for the banking examples, separate from application services."""
import base64
import hmac
import json
import os
import ssl
import time
import urllib.parse
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import jwt
from .config import ISSUER, ACCESS_TOKEN, EXCHANGE_GRANT, AGENT_ID, EXCHANGE_ID, POLICIES
from .http import DemoHandler


def issue(key, audience, scope, subject=AGENT_ID, token_use="gateway", expires=None):
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": subject, "aud": audience, "scope": scope,
              "iat": now, "exp": min(now + 300, expires) if expires else now + 300,
              "jti": str(uuid.uuid4()), "token_use": token_use}
    if token_use == "upstream": claims["act"] = {"sub": EXCHANGE_ID}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "banking-demo"})


def public_key(directory):
    jwks = json.loads((Path(directory) / "jwks.json").read_text())
    return jwt.PyJWK.from_dict(jwks["keys"][0]).key


def verify(token, key, audience, scope, token_use):
    claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER, audience=audience,
                        options={"require": ["iss", "sub", "aud", "exp", "iat"]})
    if claims.get("token_use") != token_use or scope not in claims.get("scope", "").split():
        raise ValueError("Token policy not satisfied")
    return claims



def handler(directory):
    directory = Path(directory)
    key = public_key(directory)
    signing_key = (directory / "signing.key").read_bytes()
    class Handler(DemoHandler):
        def do_GET(self):
            if self.path == "/jwks": return self.send(200, json.loads((directory / "jwks.json").read_text()))
            if self.path == "/.well-known/oauth-authorization-server":
                return self.send(200, {"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks",
                    "token_endpoint": ISSUER + "/token", "response_types_supported": [],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    "grant_types_supported": ["client_credentials", EXCHANGE_GRANT]})
            return super().do_GET()

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


        def do_POST(self):
            try: return self.token(self.read_body())
            except PermissionError: return self.send(401, {"error": "Invalid credentials"}, {"WWW-Authenticate": "Basic"})
            except (ValueError, TypeError, KeyError): return self.send(400, {"error": "Invalid demo request"})
    return Handler


def main():
    directory = Path(os.environ.get("DEMO_CREDENTIALS", "/credentials"))
    server = ThreadingHTTPServer(("0.0.0.0", 8443), handler(directory))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(directory / "tls.pem"), str(directory / "tls.key"))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__": main()
