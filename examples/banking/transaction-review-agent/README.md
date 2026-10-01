# Transaction-review A2A agent

Serves an A2A 1.0 Agent Card and synchronous `SendMessage` responses through JSON-RPC and HTTP+JSON/REST. `handler()` constructs the SDK `ExchangeTokenVerifier` for `urn:bank:backend:transaction-review` and scope `review:execute`. The HTTP adapter verifies the exchanged JWT and gateway actor before invoking `dispatch()`.

Its private `/rpc` is mapped to `/a2a/transaction-review` at the gateway. REST `POST /a2a/transaction-review/rest/message:send` maps to its private `/message:send` endpoint. Its public card is exposed at `/cards/transaction-review`. It runs with public signing keys only; it cannot mint tokens.

[review_agent.py](review_agent.py) contains the application. The folder has its own Dockerfile; build from the repository root using `docker build -f examples/banking/transaction-review-agent/Dockerfile .`.

See the [banking setup](../README.md) for credentials, gateway configuration and the local issuer/STS, and the [shared SDK](../../../sdk/python/README.md) for its enforced identity policy.

A2A 1.0 synchronous SendMessage is available through JSON-RPC and HTTP+JSON/REST. Select `binding="HTTP+JSON"` on the SDK A2A `Endpoint` and use the advertised REST base path; the token audience stays the agent gateway audience. Token exchange and backend verification are identical for both bindings.

Run the REST banking scenario: `docker compose -f examples/banking/compose.yml run --rm banking-orchestrator a2a-rest`. The `all` scenario runs both bindings.
