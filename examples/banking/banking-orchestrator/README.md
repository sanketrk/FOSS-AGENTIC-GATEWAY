# Banking orchestrator agent

Calls the transaction-review agent and one or both MCP servers through `GatewayClient`. Its code contains the banking workflow; TLS, OAuth token acquisition, MCP initialization and A2A serialization live in the shared SDK.

```sh
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator a2a
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator mcp accounts
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator mcp accounts transactions
```

It receives only its own agent credential, never the gateway's STS secret or backend tokens.

[orchestrator.py](orchestrator.py) contains the application. The folder has its own Dockerfile; build from the repository root using `docker build -f examples/banking/banking-orchestrator/Dockerfile .`.

See the [banking setup](../README.md) for credentials, gateway configuration and the local issuer/STS, and the [shared SDK](../../../sdk/python/README.md) for its enforced identity policy.

A2A 1.0 synchronous SendMessage is available through JSON-RPC and HTTP+JSON/REST. Select `binding="HTTP+JSON"` on the SDK A2A `Endpoint` and use the advertised REST base path; the token audience stays the agent gateway audience. Token exchange and backend verification are identical for both bindings.

Run the REST banking scenario: `docker compose -f examples/banking/compose.yml run --rm banking-orchestrator a2a-rest`. The `all` scenario runs both bindings.
