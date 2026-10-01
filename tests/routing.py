"""Exercise real Kong routing without needing an IdP or reachable backends."""

import json
import os
import urllib.error
import urllib.request


base = os.environ.get("GATEWAY_URL", "http://127.0.0.1:18000").rstrip("/")


def request(path, body=None):
    headers = {}
    if body is not None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "server/discover",
        }
    req = urllib.request.Request(base + path, data=body, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


body = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "server/discover",
    "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }},
}).encode()

for suffix, scope in [("", "mcp:access"),
                      ("/server-a", "mcp:server-a:access"),
                      ("/server-b", "mcp:server-b:access")]:
    endpoint = "/mcp" + suffix
    metadata_path = "/.well-known/oauth-protected-resource" + endpoint
    status, _, payload = request(metadata_path)
    assert status == 200, (metadata_path, status, payload)
    metadata = json.loads(payload)
    assert metadata["resource"] == "https://mcp.example.com" + endpoint
    assert metadata["scopes_supported"] == [scope]
    status, headers, payload = request(endpoint, body)
    assert status == 401, (endpoint, status, payload)
    challenge = headers["WWW-Authenticate"]
    assert f'resource_metadata="https://mcp.example.com{metadata_path}"' in challenge
    assert f'scope="{scope}"' in challenge

for endpoint in ["/mcp/unknown", "/mcp/server-a/", "/mcp/server-ab",
                 "/mcp/server-a/extra", "/mcp/extra", "/prefix/mcp/server-a",
                 "/.well-known/oauth-protected-resource/mcp/unknown",
                 "/.well-known/oauth-protected-resource/mcp/server-a/extra"]:
    status, _, payload = request(endpoint, body)
    assert status == 404, (endpoint, status, payload)

print("Real Kong multi-server routing and metadata isolation checks passed.")
