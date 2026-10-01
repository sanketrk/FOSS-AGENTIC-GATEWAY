"""Banking business orchestration using the shared gateway SDK."""
import argparse
import json
import os
from pathlib import Path

from foss_agentic_gateway import Endpoint, GatewayClient, OAuthClientCredentials
from banking_demo.config import ISSUER, GATEWAY, AGENT_ID, POLICIES


class BankingOrchestrator:
    def __init__(self, credentials="/credentials"):
        credentials = Path(credentials)
        ca = str(credentials / "ca.pem")
        provider = OAuthClientCredentials(token_endpoint=ISSUER + "/token", client_id=AGENT_ID,
                                         client_secret=(credentials / "agent-secret").read_text(), ca_file=ca)
        self.gateway = GatewayClient(gateway_url=GATEWAY, token_provider=provider, ca_file=ca)

    @staticmethod
    def endpoint(name):
        p = POLICIES[name]
        return Endpoint(path=p["path"], audience=p["audience"], scopes=(p["gateway_scope"],),
                        protocol="a2a" if name == "review" else "mcp")

    def review_transaction(self):
        card = self.gateway.agent_card("/cards/transaction-review")
        expected = {"url": POLICIES["review"]["audience"], "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        if expected not in card.get("supportedInterfaces", []): raise ValueError("Unexpected review-agent interface")
        return self.gateway.send_message(self.endpoint("review"), "Summarize synthetic merchant purchase DEMO-TX-003.")

    def account_overview(self, servers):
        results = {}
        for name in servers:
            tool = "get_account_summary" if name == "accounts" else "list_recent_transactions"
            result = self.gateway.call_tool(self.endpoint(name), tool, {"account_id": "DEMO-001"})
            results[name] = json.loads(result["content"][0]["text"])
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("a2a", "mcp", "all"))
    parser.add_argument("servers", nargs="*", metavar="SERVER", help="accounts and/or transactions")
    args = parser.parse_args()
    if set(args.servers) - {"accounts", "transactions"}: parser.error("servers must be accounts or transactions")
    agent = BankingOrchestrator(os.environ.get("DEMO_CREDENTIALS", "/credentials"))
    if args.scenario in ("a2a", "all"):
        print(json.dumps({"scenario": "agent-to-agent", "response": agent.review_transaction()}, indent=2))
    if args.scenario in ("mcp", "all"):
        print(json.dumps({"scenario": "agent-to-mcp", "servers": agent.account_overview(args.servers or ["accounts", "transactions"])}, indent=2))


if __name__ == "__main__": main()
