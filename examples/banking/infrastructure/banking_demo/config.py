"""Fixed, synthetic banking policies shared by the example fixtures and caller."""
ISSUER = "https://issuer:8443"
GATEWAY = "https://gateway:8443"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
AGENT_ID = "banking-orchestrator"
EXCHANGE_ID = "banking-gateway"
POLICIES = {
    "review": {"path": "/a2a/transaction-review", "resource": "urn:bank:backend:transaction-review",
               "gateway_scope": "a2a:review", "backend_scope": "review:execute"},
    "accounts": {"path": "/mcp/accounts", "resource": "urn:bank:backend:accounts",
                 "gateway_scope": "accounts:read", "backend_scope": "accounts:summary"},
    "transactions": {"path": "/mcp/transactions", "resource": "urn:bank:backend:transactions",
                     "gateway_scope": "transactions:read", "backend_scope": "transactions:recent"},
}
for policy in POLICIES.values():
    policy["audience"] = GATEWAY + policy["path"]

