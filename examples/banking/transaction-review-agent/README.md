# Transaction-review A2A agent

Serves an A2A 1.0 Agent Card and synchronous `SendMessage` response. `handler()` constructs the SDK `ExchangeTokenVerifier` for `urn:bank:backend:transaction-review` and scope `review:execute`. The HTTP adapter verifies the exchanged JWT and gateway actor before invoking `dispatch()`.

Its private `/rpc` is mapped to `/a2a/transaction-review` at the gateway. Its public card is exposed at `/cards/transaction-review`. It runs with public signing keys only; it cannot mint tokens.

[review_agent.py](review_agent.py) contains the application. The folder has its own Dockerfile; build from the repository root using `docker build -f examples/banking/transaction-review-agent/Dockerfile .`.

See the [banking setup](../README.md) for credentials, gateway configuration and the local issuer/STS, and the [shared SDK](../../../sdk/python/README.md) for its enforced identity policy.
