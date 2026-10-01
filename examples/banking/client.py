"""A deterministic banking agent that calls only gateway endpoints."""
import argparse
import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from common import ISSUER, GATEWAY, AGENT_ID, POLICIES, EXCHANGE_GRANT, ACCESS_TOKEN, EXCHANGE_ID


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class BankingAgent:
    def __init__(self, credentials="/credentials"):
        self.credentials = Path(credentials)
        context = ssl.create_default_context(cafile=str(self.credentials / "ca.pem"))
        self.http = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def request(self, url, payload=None, headers=None):
        request = urllib.request.Request(url, data=payload, headers=headers or {})
        try: response = self.http.open(request, timeout=15)
        except urllib.error.HTTPError as error: response = error
        with response:
            body = response.read()
            return response.status, json.loads(body) if body else None

    def credentials_header(self):
        credentials = urllib.parse.quote_plus(AGENT_ID) + ":" + urllib.parse.quote_plus((self.credentials / "agent-secret").read_text())
        return {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
                "Content-Type": "application/x-www-form-urlencoded"}

    def token(self, name):
        policy = POLICIES[name]
        form = {"grant_type": "client_credentials", "resource": policy["audience"], "scope": policy["gateway_scope"]}
        status, result = self.request(ISSUER + "/token", urllib.parse.urlencode(form).encode(), self.credentials_header())
        if status != 200: raise RuntimeError("Unable to obtain demo agent access token")
        return result["access_token"]

    def rpc(self, name, token, method, params, notification=False):
        policy = POLICIES[name]
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification: message["id"] = str(uuid.uuid4())
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
        if name == "review": headers["A2A-Version"] = "1.0"
        else: headers.update({"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
        return self.request(GATEWAY + policy["path"], json.dumps(message).encode(), headers)

    @staticmethod
    def checked(status, result):
        if status != 200 or "error" in result: raise RuntimeError("Gateway example request failed")
        return result["result"]

    @staticmethod
    def check_identity(name, data):
        identity = data["verified_upstream_identity"]
        policy = POLICIES[name]
        assert identity == {"subject": AGENT_ID, "actor": EXCHANGE_ID, "audience": policy["resource"],
                            "scope": policy["backend_scope"], "token_use": "upstream"}, identity

    def a2a(self):
        status, card = self.request(GATEWAY + "/cards/transaction-review", headers={"A2A-Version": "1.0"})
        assert status == 200 and card["supportedInterfaces"][0]["url"] == POLICIES["review"]["audience"]
        message = {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER",
                               "parts": [{"text": "Summarize synthetic merchant purchase DEMO-TX-003."}]}}
        result = self.checked(*self.rpc("review", self.token("review"), "SendMessage", message))
        self.check_identity("review", result["message"]["metadata"])
        print(json.dumps({"scenario": "agent-to-agent", "response": result["message"]}, indent=2))

    def mcp(self, servers):
        results = {}
        for name in servers:
            policy, token = POLICIES[name], self.token(name)
            status, metadata = self.request(GATEWAY + "/.well-known/oauth-protected-resource" + policy["path"])
            assert status == 200 and metadata["resource"] == policy["audience"]
            initialized = self.checked(*self.rpc(name, token, "initialize", {"protocolVersion": "2025-11-25",
                "capabilities": {}, "clientInfo": {"name": AGENT_ID, "version": "1.0.0"}}))
            assert initialized["protocolVersion"] == "2025-11-25"
            assert self.rpc(name, token, "notifications/initialized", {}, notification=True)[0] == 202
            tools = self.checked(*self.rpc(name, token, "tools/list", {}))["tools"]
            tool = "get_account_summary" if name == "accounts" else "list_recent_transactions"
            assert any(item["name"] == tool for item in tools)
            result = self.checked(*self.rpc(name, token, "tools/call", {"name": tool, "arguments": {"account_id": "DEMO-001"}}))
            assert not result["isError"]
            data = json.loads(result["content"][0]["text"])
            self.check_identity(name, data)
            results[name] = data
        print(json.dumps({"scenario": "agent-to-mcp", "servers": results}, indent=2))

    def boundaries(self):
        # A token issued for accounts must never work at transactions.
        token = self.token("accounts")
        assert self.rpc("transactions", token, "tools/list", {})[0] == 401
        assert self.rpc("review", "", "SendMessage", {})[0] == 401
        # Caller credentials cannot invoke the STS's gateway-only exchange grant.
        form = {"grant_type": EXCHANGE_GRANT, "subject_token": token, "subject_token_type": ACCESS_TOKEN,
                "requested_token_type": ACCESS_TOKEN, "resource": POLICIES["accounts"]["resource"],
                "scope": POLICIES["accounts"]["backend_scope"]}
        assert self.request(ISSUER + "/token", urllib.parse.urlencode(form).encode(), self.credentials_header())[0] == 401
        print("Passed: wrong gateway audience, missing bearer, and unauthorized STS client are rejected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("a2a", "mcp", "all"))
    parser.add_argument("servers", nargs="*", metavar="SERVER", help="accounts and/or transactions")
    args = parser.parse_args()
    if set(args.servers) - {"accounts", "transactions"}: parser.error("servers must be accounts or transactions")
    agent = BankingAgent(os.environ.get("DEMO_CREDENTIALS", "/credentials"))
    if args.scenario in ("a2a", "all"): agent.a2a()
    if args.scenario in ("mcp", "all"): agent.mcp(args.servers or ["accounts", "transactions"])
    if args.scenario == "all": agent.boundaries()
