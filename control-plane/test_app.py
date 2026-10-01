import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from app import KubernetesPublisher, Registry, generate, handler, validate

TOKEN = "test-admin-token-" + "x" * 32


def sample(server_id="server-a"):
    return {"id": server_id, "name": "Test server", "upstream_url": "http://internal:3000/mcp",
            "issuer": "https://issuer.example.com/", "discovery_url": "https://issuer.example.com/.well-known/openid-configuration",
            "audience": "https://gateway.example.com/mcp/" + server_id, "required_scopes": ["mcp:" + server_id + ":access"]}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "registry.sqlite3")
        self.registry = Registry(self.path, "https://gateway.example.com")

    def tearDown(self):
        self.registry.db.close()
        self.temp.cleanup()

    def test_persistence_and_duplicate_policy(self):
        self.registry.save(sample(), create=True)
        with self.assertRaises(FileExistsError): self.registry.save(sample(), create=True)
        other = sample("server-b"); other["audience"] = "https://gateway.example.com/mcp/server-a"
        with self.assertRaises(ValueError): self.registry.save(other, create=True)
        again = Registry(self.path, "https://gateway.example.com")
        self.assertEqual(again.servers()[0]["id"], "server-a")
        again.db.close()

    def test_audience_is_bound_to_registered_public_resource(self):
        data = sample()
        data['audience'] = 'https://unrelated.example.com/api'
        with self.assertRaises(ValueError): self.registry.save(data, create=True)
        self.assertEqual(self.registry.servers(), [])

    def test_generated_metadata_and_mapping(self):
        config = generate([validate(sample()), validate(sample("server-b"))], "https://gateway.example.com")
        route = config["services"][0]["routes"][0]
        self.assertTrue(route["strip_path"])
        self.assertEqual(route["paths"], ["~/mcp/server-a$", r"~/\.well-known/oauth-protected-resource/mcp/server-a$"])
        policy = route["plugins"][0]["config"]
        self.assertEqual(policy["resource_url"], "https://gateway.example.com/mcp/server-a")
        self.assertEqual(policy["audience"], policy["resource_url"])
        self.assertTrue(policy["ssl_verify"])
        self.assertFalse(policy["forward_bearer_token"])
        self.assertEqual(config["services"][0]["url"], "http://internal:3000/mcp")

    def test_invalid_registration(self):
        for patch in [{"id": "../escape"}, {"id": "a$"}, {"upstream_url": "file:///etc/passwd"},
                      {"upstream_url": "http://user:secret@internal/mcp"}, {"issuer": "http://issuer/"},
                      {"audience": 'bad"audience'}, {"required_scopes": []}, {"required_scopes": ['bad"scope']},
                      {"allowed_origins": ["https://client/path"]}, {"legacy_enabled": "false"}, {"unknown": True}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError): validate({**sample(), **patch})

    def test_publish_review_conflict_and_failure(self):
        calls = []
        self.registry.publisher = lambda snapshot: calls.append(snapshot) or {"state": "rollout_requested"}
        with self.assertRaises(ValueError): self.registry.publish(self.registry.preview()["revision"])
        self.registry.save(sample(), create=True)
        revision = self.registry.preview()["revision"]
        self.registry.save(sample("server-b"), create=True)
        with self.assertRaises(FileExistsError): self.registry.publish(revision)
        self.assertFalse(calls)
        new_revision = self.registry.preview()["revision"]
        self.registry.publish(new_revision)
        self.assertEqual(self.registry.status()["published"]["revision"], new_revision)
        def fail(snapshot): raise OSError("API unavailable")
        self.registry.publisher = fail
        with self.assertRaises(OSError): self.registry.publish(new_revision)
        self.assertEqual(self.registry.status()["events"][0]["action"], "publish_failed")
        self.registry.delete("server-a")
        self.assertNotEqual(self.registry.status()["draft_revision"], new_revision)


class PublisherTests(unittest.TestCase):
    def test_configmap_then_rollout(self):
        publisher = object.__new__(KubernetesPublisher)
        publisher.namespace = "test"
        publisher.configmap = "mcp-gateway-kong"
        publisher.deployment = "mcp-gateway"
        calls = []
        publisher.call = lambda path, data=None: calls.append((path, data)) or {}
        snapshot = {"revision": "reviewed", "config": generate([validate(sample())], "https://gateway.example.com")}
        result = publisher(snapshot)
        self.assertEqual(len(calls), 4)
        self.assertEqual(json.loads(calls[2][1]["data"]["kong.yml"]), snapshot["config"])
        self.assertEqual(calls[3][1]["spec"]["template"]["metadata"]["annotations"]["mcp-gateway/config-revision"], "reviewed")
        self.assertEqual(result["state"], "rollout_requested")

    def test_preflight_failure_does_not_mutate(self):
        publisher = object.__new__(KubernetesPublisher)
        publisher.namespace, publisher.configmap, publisher.deployment = "test", "config", "gateway"
        def fail(path, data=None):
            self.assertIsNone(data)
            raise OSError("No permission")
        publisher.call = fail
        with self.assertRaises(OSError): publisher({"config": {}, "revision": "abc"})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry(":memory:", "https://gateway.example.com")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.registry, TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.registry.db.close()

    def request(self, path, method="GET", data=None, authenticated=True, headers=None):
        merged = {"Content-Type": "application/json"}
        if authenticated: merged["Authorization"] = "Bearer " + TOKEN
        merged.update(headers or {})
        req = urllib.request.Request(self.base + path, method=method, headers=merged,
                                     data=json.dumps(data).encode() if data is not None else None)
        try: response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as error: response = error
        with response: return response.status, response.headers, response.read()

    def test_auth_and_origin(self):
        self.assertEqual(self.request("/api/servers", authenticated=False)[0], 401)
        self.assertEqual(self.request("/api/servers", headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("/healthz", authenticated=False)[0], 200)
        code, headers, body = self.request("/", authenticated=False)
        self.assertEqual(code, 200); self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(TOKEN.encode(), body)

    def test_crud_and_export(self):
        self.assertEqual(self.request("/api/servers", "POST", sample())[0], 201)
        self.assertEqual(self.request("/api/servers", "POST", sample())[0], 409)
        self.assertEqual(self.request("/api/servers/server-a", "PUT", {**sample(), "name": "Updated"})[0], 200)
        self.assertEqual(self.request("/api/servers/server-b", "PUT", sample())[0], 400)
        code, _, body = self.request("/api/preview")
        self.assertEqual(code, 200); preview = json.loads(body)
        self.assertEqual(self.request("/api/publish", "POST", {"revision": preview["revision"]})[0], 400)
        self.assertEqual(self.request("/api/servers/server-a", "DELETE")[0], 200)
        self.assertEqual(self.request("/api/servers/server-a", "DELETE")[0], 404)
        self.assertEqual(json.loads(self.request("/api/servers")[2])["servers"], [])

    def test_content_type_and_input_rejection(self):
        self.assertEqual(self.request("/api/servers", "POST", sample(), headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/servers", "POST", {**sample(), "upstream_url": "ftp://internal"})[0], 400)
        self.assertEqual(self.request("/api/publish", "POST", ["invalid"])[0], 400)


if __name__ == "__main__": unittest.main()
