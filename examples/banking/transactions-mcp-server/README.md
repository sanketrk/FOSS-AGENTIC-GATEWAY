# Transactions MCP server

Owns three synthetic transactions and the read-only `list_recent_transactions` tool. The SDK verifier requires audience `urn:bank:backend:transactions`, scope `transactions:recent`, and actor `banking-gateway` before any MCP method executes.

Its private `/mcp` endpoint is mapped to `/mcp/transactions` at the gateway. A token issued for the account server is rejected. It runs with public signing keys only.

[transactions_server.py](transactions_server.py) contains the application. The folder has its own Dockerfile; build from the repository root using `docker build -f examples/banking/transactions-mcp-server/Dockerfile .`.

See the [banking setup](../README.md) for credentials, gateway configuration and the local issuer/STS, and the [shared SDK](../../../sdk/python/README.md) for its enforced identity policy.
