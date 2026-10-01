"""FOSS-AGENTIC-GATEWAY administrator registry and Kong publisher."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from oidc import OIDCClient, AuthError

ROOT = Path(__file__).parent
MAX_BODY = 65536


def url(value, https=False, origin=False):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("URL must be a string under 2048 characters")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in (["https"] if https else ["http", "https"])
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError("URL must have a host, allowed scheme, and no credentials, query or fragment")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid URL port") from None
    if origin and parsed.path not in ("", "/"):
        raise ValueError("Public base URL must have no path")
    return value.rstrip("/") if origin else value


def validate(data):
    fields = {"id", "name", "upstream_url", "issuer", "discovery_url", "audience",
              "required_scopes", "allowed_origins", "legacy_enabled"}
    if not isinstance(data, dict) or set(data) - fields:
        raise ValueError("Unknown fields or invalid server object")
    for key in ("id", "name", "audience"):
        if not isinstance(data.get(key), str) or not data[key].strip() or len(data[key]) > 128:
            raise ValueError(f"{key} is required and must be under 128 characters")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", data["id"]):
        raise ValueError("id must be a lowercase slug, up to 63 characters")
    if not re.fullmatch(r"[A-Za-z0-9:/._-]+", data["audience"]):
        raise ValueError("Invalid audience")
    result = {key: data[key] for key in ("id", "name", "audience")}
    result["upstream_url"] = url(data.get("upstream_url"))
    for key in ("issuer", "discovery_url"):
        result[key] = url(data.get(key), https=True)
    scopes = data.get("required_scopes")
    if (not isinstance(scopes, list) or not scopes or len(scopes) > 32
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9:/._-]{1,128}", s) for s in scopes)):
        raise ValueError("At least one valid required scope is needed")
    result["required_scopes"] = list(dict.fromkeys(scopes))
    origins = data.get("allowed_origins", [])
    if not isinstance(origins, list) or len(origins) > 32:
        raise ValueError("allowed_origins must be an array of up to 32 origins")
    result["allowed_origins"] = [url(v, https=True, origin=True) for v in origins]
    legacy = data.get("legacy_enabled", True)
    if not isinstance(legacy, bool):
        raise ValueError("legacy_enabled must be a boolean")
    result["legacy_enabled"] = legacy
    return result


def generate(servers, public_url):
    services = []
    for server in servers:
        endpoint = "/mcp/" + server["id"]
        metadata = "/.well-known/oauth-protected-resource" + endpoint
        services.append({
            "name": "mcp-" + server["id"], "url": server["upstream_url"],
            "connect_timeout": 5000, "read_timeout": 3600000, "write_timeout": 3600000,
            "retries": 0, "routes": [{
                "name": "mcp-" + server["id"] + "-http",
                "paths": ["~" + endpoint + "$", "~" + metadata.replace(".", r"\.") + "$"],
                "strip_path": True, "path_handling": "v0", "preserve_host": False,
                "request_buffering": False, "response_buffering": False,
                "plugins": [{"name": "mcp-gateway", "config": {
                    "resource_url": public_url + endpoint,
                    "resource_metadata_url": public_url + metadata,
                    "metadata_paths": [metadata],
                    "authorization_servers": [{"issuer": server["issuer"], "discovery_url": server["discovery_url"]}],
                    "audience": public_url + endpoint, "required_scopes": server["required_scopes"],
                    "scopes_supported": server["required_scopes"],
                    "allowed_origins": server["allowed_origins"], "forward_bearer_token": False,
                    "ssl_verify": True, "signing_algorithms": ["RS256"],
                    "legacy_protocol_versions": ["2025-11-25", "2025-03-26"] if server["legacy_enabled"] else [],
                }}],
            }],
        })
    # JSON is also valid YAML and can be loaded as Kong's declarative config.
    return {"_format_version": "3.0", "_transform": True, "services": services}


class Registry:
    def __init__(self, database, public_url, publisher=None):
        self.public_url = url(public_url, https=True, origin=True)
        self.publisher = publisher
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS servers (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp REAL, action TEXT, details TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        self.db.commit()

    def audit(self, action, details):
        self.db.execute("INSERT INTO events(timestamp,action,details) VALUES(?,?,?)", (time.time(), action, json.dumps(details)))

    def servers(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT document FROM servers ORDER BY id")]

    def save(self, data, create=False, actor=None):
        server = validate(data)
        expected_audience = self.public_url + "/mcp/" + server["id"]
        if server["audience"] != expected_audience:
            raise ValueError("Token audience must match the server's public MCP resource URI")
        with self.lock, self.db:
            exists = self.db.execute("SELECT 1 FROM servers WHERE id=?", (server["id"],)).fetchone()
            if create and exists:
                raise FileExistsError("Server ID already exists")
            if not create and not exists:
                raise KeyError("Server not found")
            for other in self.servers():
                if other["id"] != server["id"] and other["audience"] == server["audience"]:
                    raise ValueError("Each server must use a distinct audience")
            self.db.execute("INSERT OR REPLACE INTO servers VALUES(?,?)", (server["id"], json.dumps(server)))
            self.audit("create" if create else "update", {"server": server["id"], "actor": actor})
        return server

    def delete(self, server_id, actor=None):
        with self.lock, self.db:
            if self.db.execute("DELETE FROM servers WHERE id=?", (server_id,)).rowcount != 1:
                raise KeyError("Server not found")
            self.audit("delete", {"server": server_id, "actor": actor})

    def preview(self):
        with self.lock:
            config = generate(self.servers(), self.public_url)
            digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            return {"revision": digest, "config": config}

    def status(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key='published'").fetchone()
            events = self.db.execute("SELECT timestamp,action,details FROM events ORDER BY id DESC LIMIT 50").fetchall()
            return {"publish_enabled": self.publisher is not None,
                    "published": json.loads(row[0]) if row else None,
                    "draft_revision": self.preview()["revision"],
                    "events": [{"timestamp": t, "action": a, "details": json.loads(d)} for t, a, d in events]}

    def publish(self, revision, actor=None):
        with self.lock:
            snapshot = self.preview()
            if revision != snapshot["revision"]:
                raise FileExistsError("Draft changed; review the latest preview before publishing")
            if not self.publisher:
                raise ValueError("Publishing is disabled; download the config for a manual rollout")
            if not snapshot["config"]["services"]:
                raise ValueError("Register at least one server before publishing")
            try:
                result = self.publisher(snapshot)
            except Exception:
                with self.db:
                    self.audit("publish_failed", {"revision": revision, "actor": actor})
                raise
            published = {"revision": revision, "timestamp": time.time(), **result}
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO state VALUES('published',?)", (json.dumps(published),))
                self.audit("publish_requested", {**published, "actor": actor})
            return published


class KubernetesPublisher:
    """Writes one ConfigMap and requests a rolling deployment; never calls Kong Admin API."""
    def __init__(self, namespace, configmap="foss-agentic-gateway-kong", deployment="foss-agentic-gateway"):
        self.namespace, self.configmap, self.deployment = namespace, configmap, deployment
        self.base = "https://" + os.environ["KUBERNETES_SERVICE_HOST"] + ":" + os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.account = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.context = ssl.create_default_context(cafile=str(self.account / "ca.crt"))

    def call(self, path, data=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data else None,
                                     method="PATCH" if data else "GET", headers={
                                         "Authorization": "Bearer " + (self.account / "token").read_text().strip(),
                                         "Content-Type": "application/merge-patch+json",
                                     })
        with urllib.request.urlopen(req, context=self.context, timeout=15) as response:
            return json.load(response)

    def __call__(self, snapshot):
        prefix = "/api/v1/namespaces/" + self.namespace + "/configmaps/" + self.configmap
        # GET both objects before any mutation to catch missing resources/permissions.
        self.call(prefix)
        deployment = "/apis/apps/v1/namespaces/" + self.namespace + "/deployments/" + self.deployment
        self.call(deployment)
        self.call(prefix, {"data": {"kong.yml": json.dumps(snapshot["config"], indent=2)}})
        self.call(deployment, {"spec": {"template": {"metadata": {"annotations": {
            "foss-agentic-gateway/config-revision": snapshot["revision"],
            "foss-agentic-gateway/publish-time": str(time.time()),
        }}}}})
        return {"state": "rollout_requested", "deployment": self.deployment}


def handler(registry, token=None, oidc=None):
    if oidc is None and not token:
        raise ValueError("An authentication provider is required")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Avoid logging request bodies, headers, credentials, or URL query strings.
            pass

        def send(self, code, data, content_type="application/json", headers=None, cookies=()):
            payload = json.dumps(data).encode() if content_type == "application/json" else data
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (headers or {}).items(): self.send_header(key, value)
            for cookie in cookies: self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(payload)

        def dispatch(self):
            try:
                return self._dispatch()
            except AuthError as error:
                return self.send(error.status, {"error": str(error)})
            except Exception:
                return self.send(502, {"error": "Identity provider unavailable or login failed. Please retry."})

        def _dispatch(self):
            path = urllib.parse.urlsplit(self.path).path
            if self.command == "GET" and path == "/healthz":
                return self.send(200, {"status": "ok"})
            if self.command == "GET" and path == "/auth/config":
                return self.send(200, {"mode": "oidc" if oidc else "token", "provider": "OIDC" if oidc else None})
            if oidc and path == "/auth/login" and self.command == "GET":
                location, cookie = oidc.login()
                return self.send(302, {}, headers={"Location": location}, cookies=[cookie])
            if oidc and path == "/auth/callback" and self.command == "GET":
                session, cookies = oidc.callback(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query), self.headers)
                with registry.lock, registry.db: registry.audit("login", {"actor": session["sub"]})
                return self.send(302, {}, headers={"Location": "/"}, cookies=cookies)
            if oidc and path == "/auth/logout" and self.command == "POST":
                session, cookie, location = oidc.logout(self.headers)
                with registry.lock, registry.db: registry.audit("logout", {"actor": session["sub"]})
                return self.send(200, {"logout_url": location}, cookies=[cookie])
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.command == "GET" and path in assets:
                filename, content_type = assets[path]
                return self.send(200, (ROOT / "static" / filename).read_bytes(), content_type)
            if not path.startswith("/api/"):
                return self.send(404, {"error": "Not found"})
            origin = self.headers.get("Origin")
            if oidc:
                if origin and origin != oidc.origin:
                    return self.send(403, {"error": "Cross-origin requests are forbidden"})
                identity = oidc.authenticate(self.headers, mutation=self.command != "GET")
                actor = identity["sub"]
            else:
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                    return self.send(401, {"error": "Administrator token required"})
                if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
                    return self.send(403, {"error": "Cross-origin requests are forbidden"})
                identity = {"sub": "local-admin-token", "name": "Local administrator", "csrf": None}
                actor = identity["sub"]
            if path == "/api/session" and self.command == "GET":
                return self.send(200, identity)
            try:
                data = None
                if self.command in ("POST", "PUT"):
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        return self.send(415, {"error": "Use application/json"})
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY:
                        return self.send(413, {"error": "Request body exceeds limit or is empty"})
                    data = json.loads(self.rfile.read(length))
                if path == "/api/servers" and self.command == "GET":
                    return self.send(200, {"servers": registry.servers(), "public_url": registry.public_url})
                if path == "/api/servers" and self.command == "POST":
                    return self.send(201, registry.save(data, create=True, actor=actor))
                match = re.fullmatch(r"/api/servers/([a-z][a-z0-9-]{0,62})", path)
                if match and self.command == "PUT":
                    if not isinstance(data, dict) or data.get("id") != match[1]:
                        raise ValueError("Server ID must match URL")
                    return self.send(200, registry.save(data, actor=actor))
                if match and self.command == "DELETE":
                    registry.delete(match[1], actor=actor)
                    return self.send(200, {"deleted": match[1]})
                if path == "/api/preview" and self.command == "GET":
                    return self.send(200, registry.preview())
                if path == "/api/status" and self.command == "GET":
                    return self.send(200, registry.status())
                if path == "/api/publish" and self.command == "POST":
                    if not isinstance(data, dict) or set(data) != {"revision"}:
                        raise ValueError("Provide the reviewed revision")
                    return self.send(202, registry.publish(data["revision"], actor=actor))
                return self.send(404, {"error": "Not found"})
            except FileExistsError as error:
                self.send(409, {"error": str(error)})
            except KeyError:
                self.send(404, {"error": "Server not found"})
            except (ValueError, TypeError) as error:
                self.send(400, {"error": str(error)})
            except Exception:
                self.send(502, {"error": "Operation failed; publishing may be partially applied. Check cluster state and retry the reviewed revision."})

        def do_GET(self): self.dispatch()
        def do_POST(self): self.dispatch()
        def do_PUT(self): self.dispatch()
        def do_DELETE(self): self.dispatch()
    return Handler


def main():
    mode = os.environ.get("AUTH_MODE", "oidc")
    token, oidc = None, None
    if mode == "oidc":
        oidc = OIDCClient(
            os.environ.get("OIDC_ISSUER", ""), os.environ.get("OIDC_CLIENT_ID", ""),
            os.environ.get("OIDC_CLIENT_SECRET", ""), os.environ.get("OIDC_API_AUDIENCE") or None,
            os.environ.get("CONTROL_PLANE_PUBLIC_URL", ""),
            admin_claim=os.environ.get("OIDC_ADMIN_CLAIM", "/roles"),
            admin_value=os.environ.get("OIDC_ADMIN_VALUE", "agentic-admin"),
            admin_claim_source=os.environ.get("OIDC_ADMIN_CLAIM_SOURCE", "id_token"),
            scopes=os.environ.get("OIDC_SCOPES", "openid profile email").split(),
            token_auth_method=os.environ.get("OIDC_TOKEN_AUTH_METHOD", "client_secret_basic"),
            authorization_params=json.loads(os.environ.get("OIDC_AUTHORIZATION_PARAMS", "{}")),
            discovery_url=os.environ.get("OIDC_DISCOVERY_URL") or None,
            resource=os.environ.get("OIDC_RESOURCE") or None,
            signing_algorithms=os.environ.get("OIDC_SIGNING_ALGORITHMS", "RS256").split(","),
            session_seconds=os.environ.get("SESSION_SECONDS", "900"))
    elif mode == "token":
        token = os.environ.get("CONTROL_PLANE_TOKEN", "")
        if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
            raise SystemExit("CONTROL_PLANE_TOKEN must contain at least 32 ASCII characters without whitespace")
    else:
        raise SystemExit("AUTH_MODE must be oidc or token")
    database = os.environ.get("REGISTRY_DATABASE", "registry.sqlite3")
    namespace = os.environ.get("PUBLISH_NAMESPACE")
    publisher = KubernetesPublisher(namespace,
        configmap=os.environ.get("PUBLISH_CONFIGMAP", "foss-agentic-gateway-kong"),
        deployment=os.environ.get("PUBLISH_DEPLOYMENT", "foss-agentic-gateway")) if namespace else None
    registry = Registry(database, os.environ["GATEWAY_PUBLIC_URL"], publisher)
    server = ThreadingHTTPServer((os.environ.get("CONTROL_PLANE_HOST", "127.0.0.1"), int(os.environ.get("PORT", "8080"))), handler(registry, token=token, oidc=oidc))
    server.serve_forever()


if __name__ == "__main__":
    main()
