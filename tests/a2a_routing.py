"""Check A2A route and authentication boundaries against real Kong."""
import json
import urllib.request
import urllib.error

def request(path, body=None, version=None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if version is not None:
        headers["A2A-Version"] = version
    req = urllib.request.Request("http://127.0.0.1:18000" + path, data=body, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()

body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "task-1"}}).encode()
status, headers, payload = request("/a2a/agent-a", body, "1.0")
assert status == 401 and headers["WWW-Authenticate"] == "Bearer", (status, payload)
status, _, payload = request("/a2a/agent-a", body, "0.3")
assert status == 401, (status, payload)
status, _, payload = request("/a2a/agent-a", body, "2.0")
assert status == 400 and json.loads(payload)["error"]["code"] == -32009, (status, payload)
status, _, payload = request("/a2a/agent-a", b"{", "1.0")
assert status == 400 and json.loads(payload)["error"]["code"] == -32700, (status, payload)
status, _, payload = request("/a2a/agent-a")
assert status == 405, (status, payload)
# Runtime test fixture protects its card; public card proxying is covered separately.
status, _, payload = request("/.well-known/agent-card.json")
assert status == 401, (status, payload)
for path in ("/a2a/agent-a/", "/a2a/agent-ab", "/a2a/unknown"):
    status, _, payload = request(path, body, "1.0")
    assert status == 404, (path, status, payload)
print("Real Kong A2A routing, versioning, and authentication checks passed.")
