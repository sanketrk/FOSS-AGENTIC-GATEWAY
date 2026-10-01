# Accounts MCP server

Owns the synthetic account data and the read-only `get_account_summary` tool. The SDK verifier requires audience `urn:bank:backend:accounts`, scope `accounts:summary`, and actor `banking-gateway` before any MCP method executes.

Its private `/mcp` endpoint is mapped to `/mcp/accounts` at the gateway. It accepts only synthetic account `DEMO-001` and runs with public signing keys only.

[accounts_server.py](accounts_server.py) contains the application. The folder has its own Dockerfile; build from the repository root using `docker build -f examples/banking/accounts-mcp-server/Dockerfile .`.

See the [banking setup](../README.md) for credentials, gateway configuration and the local issuer/STS, and the [shared SDK](../../../sdk/python/README.md) for its enforced identity policy.
