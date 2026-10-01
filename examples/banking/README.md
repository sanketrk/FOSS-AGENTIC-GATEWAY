# Banking examples: agents, MCP, and token exchange

Run a banking orchestrator through Open Agentic Gateway in three ways:

1. **Agent → agent:** request a transaction summary from an A2A transaction-review agent.
2. **Agent → one MCP server:** call `get_account_summary` on the account service.
3. **Agent → multiple MCP servers:** combine an account summary with `list_recent_transactions` from a separate service.

Every authenticated route uses the generic `token-exchange` plugin. The caller gets a gateway access token; the gateway validates it and requests a different backend token using the [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) exchange grant. The caller never receives the exchanged token or the gateway's STS credentials.

The orchestrator is a command-line A2A/MCP client; the review agent is the A2A server. The scenarios are independent: MCP results are not passed to the review agent, which returns a fixed summary.

All account and transaction data is synthetic. The orchestrator and review agent are deterministic Python examples, with no LLM dependency. They make no payments and implement only the protocol methods used in these examples.

## What runs

```text
Banking orchestrator -> Gateway: request + caller token
Gateway -> Token-exchange plugin: verified caller + selected target + required scope
Token-exchange plugin -> Local STS: request a new token for that target and scope
Local STS -> Token-exchange plugin: authorize and mint limited backend token
Token-exchange plugin -> Gateway: replace outgoing token
Gateway -> Selected agent or MCP server: request + limited backend token
Selected backend: verify token and permission, then execute
Selected backend -> Gateway -> Banking orchestrator: result
```

The local OAuth issuer signs short-lived gateway JWTs and implements client credentials, discovery/JWKS, and the token-exchange requests needed here. It is a test fixture, not a complete OIDC provider. TLS uses a generated demo CA that containers explicitly trust; verification stays enabled. No external IdP or preexisting credentials are required.

| Destination | Gateway audience | Gateway scope | Exchanged backend audience | Backend scope |
| --- | --- | --- | --- | --- |
| Review agent | `https://gateway:8443/a2a/transaction-review` | `a2a:review` | `urn:bank:backend:transaction-review` | `review:execute` |
| Accounts MCP | `https://gateway:8443/mcp/accounts` | `accounts:read` | `urn:bank:backend:accounts` | `accounts:summary` |
| Transactions MCP | `https://gateway:8443/mcp/transactions` | `transactions:read` | `urn:bank:backend:transactions` | `transactions:recent` |

The demo uses an agent identity obtained through client credentials. Each normal gateway token already has one route-specific scope; exchange rebinds its audience and maps that scope to the target permission. There is no human login or user-entitlement directory in this fixture. A separate test supplies a signed synthetic subject token with 100 scopes and checks that the STS issues only the one configured target scope.

The STS checks the gateway client, the subject token's issuer/signature/expiry/audience/scope, and the requested resource/scope against fixed policy. Its backend token preserves `sub=banking-orchestrator`, records `act.sub=banking-gateway`, and changes the audience and scope for the destination. Backends independently verify that token. The original gateway token is rejected by every backend.

## Start the demo

Use Docker with Compose v2. Run these commands from the repository root:

```sh
docker compose -f examples/banking/compose.yml --profile tools build
docker compose -f examples/banking/compose.yml run --rm prepare
docker compose -f examples/banking/compose.yml up -d --wait issuer review-agent accounts transactions gateway
```

`prepare` generates disposable keys, certificates, and random client secrets into separate named volumes. Only the issuer receives its signing key. The gateway receives its own exchange credential, the caller receives only its agent credential, and backends receive public verification keys. Initialization runs without a network. Stop existing demo containers before running `prepare` again, since it rotates these credentials.

The `banking-orchestrator` runs on the Compose `agents` network and uses `https://gateway:8443`; that DNS name is internal to Compose. There is no host DNS configuration to perform. The gateway's HTTPS port is also bound to `127.0.0.1:8443` for local inspection. The CA is not installed into the host trust store.

## 1. An agent calls another agent

```sh
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator a2a
```

The orchestrator fetches the public card at `/cards/transaction-review`, acquires a JWT for the gateway A2A audience, and sends `SendMessage` with `A2A-Version: 1.0`. The gateway exchanges that JWT for the review agent's resource and calls its private `/rpc` endpoint. The responder returns an A2A `SendMessageResponse.message` summarizing synthetic merchant purchase `DEMO-TX-003`.

The example uses the JSON-RPC and HTTP+JSON/REST bindings of [A2A 1.0](https://a2a-protocol.org/latest/specification/). Its card advertises the public gateway interface and Bearer JWT security. The card is anonymous and skips exchange; `SendMessage` is authenticated and exchanged. Streaming, task persistence, and push notifications are not part of this fixture.

### A2A over REST

```sh
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator a2a-rest
```

The card also advertises the REST base `/a2a/transaction-review/rest`. The SDK posts a plain `SendMessageRequest` to `/message:send` below that base; the gateway forwards it to the backend `/message:send`. The gateway audience and exchange policy are the same as JSON-RPC. The `all` command runs both bindings.

## 2. An agent calls one MCP server

```sh
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator mcp accounts
```

The orchestrator reads OAuth protected-resource metadata, obtains an account-specific gateway token, and performs `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. It requests `get_account_summary` for synthetic account `DEMO-001`, returning its INR balance and status.

The MCP fixtures use the gateway's [2025-11-25 Streamable HTTP compatibility profile](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), with POST JSON responses and a stateless server. Requests send `Accept: application/json, text/event-stream`; notifications return HTTP 202. These examples do not exercise the newer POST-only profile or SSE resumption.

## 3. An agent calls multiple MCP servers

```sh
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator mcp accounts transactions
```

The same orchestrator obtains a separate gateway token for each MCP audience, opens each server independently, and combines the account summary with three synthetic recent transactions. Each request is exchanged for that server's backend audience and scopes. An account token cannot be reused against the transactions endpoint.

Successful A2A `SendMessage` responses and MCP tool results include a demonstration `verified_upstream_identity` receipt. For accounts it looks like:

```json
{
  "subject": "banking-orchestrator",
  "audience": "urn:bank:backend:accounts",
  "scope": "accounts:summary",
  "actor": "banking-gateway",
  "token_use": "upstream"
}
```

Agent Cards, initialization, notifications, and tool discovery do not include the receipt. The receipt is fixture output after backend JWT verification, not an identity header inserted by the gateway. Bearer tokens and secrets are never printed.

## Verify and stop

Run all scenarios:

```sh
docker compose -f examples/banking/compose.yml run --rm banking-orchestrator all
```

The responders mandate SDK verification before dispatch. CI integration tests assert that exchanged identities match each backend and that a wrong gateway audience is rejected. Fixture tests also verify that broad subject scopes are reduced to one target scope, that a missing required caller scope blocks exchange, that backends reject original gateway tokens and tokens for a different backend, and that the STS rejects expired subjects and expanded scopes. CI runs both these tests and this real Kong/Compose demo.

The backend services have no published ports and join only the internal `backends` network; the caller joins only `agents`. Backend HTTP is isolated to this local demo network. Use your deployment's authenticated encrypted backend transport and network controls when adapting the topology.

```sh
docker compose -f examples/banking/compose.yml down -v
```

This removes only the demo's containers, networks, and credential volumes.

## Four standalone components and the SDK

```text
examples/banking/
  banking-orchestrator/         Calling agent: SDK-based A2A/MCP orchestration
  transaction-review-agent/    Receiving A2A agent: review business logic
  accounts-mcp-server/         MCP server: account data and summary tool
  transactions-mcp-server/     MCP server: transaction data and recent-transactions tool
  infrastructure/              Local issuer/STS, preparation, policies and HTTP adapter
  compose.yml                  Separate images, networks and credential mounts
  kong.yml                     A2A/MCP routes and mandatory exchange plugin configuration
sdk/python/                    Shared installable gateway SDK
```

Each of the four application folders owns its code, README and Dockerfile. Each runs as a separate process and Docker image. Shared OAuth/JWT/protocol behavior lives in the [SDK](../../sdk/python/README.md), which is installed into each application image. Infrastructure is separate from application business logic.

| Component | Application source | SDK integration |
| --- | --- | --- |
| [Banking orchestrator](banking-orchestrator/README.md) | [orchestrator.py](banking-orchestrator/orchestrator.py) | `GatewayClient.send_message()` and `call_tool()` |
| [Review agent](transaction-review-agent/README.md) | [review_agent.py](transaction-review-agent/review_agent.py) | `ExchangeTokenVerifier` before A2A dispatch |
| [Accounts server](accounts-mcp-server/README.md) | [accounts_server.py](accounts-mcp-server/accounts_server.py) | `ExchangeTokenVerifier` before MCP dispatch |
| [Transactions server](transactions-mcp-server/README.md) | [transactions_server.py](transactions-mcp-server/transactions_server.py) | `ExchangeTokenVerifier` before MCP dispatch |

The fixture STS issues exactly one backend scope per target. The SDK checks for required scopes and accepts additional signed scopes; narrow issuance remains the STS policy.

The SDK client permits gateway-relative endpoints over verified HTTPS. Each backend requires a trusted signature, its own audience/scopes, and the signed gateway actor claim `act.sub=banking-gateway`. Original tokens and tokens lacking that actor are rejected before business logic. The gateway performs RFC 8693 exchange; the caller does not receive the gateway exchange credential. Receipts remain diagnostic output and are never used as security proof. See the [SDK exchange policy](../../sdk/python/README.md#backend-mandate-the-exchange-policy-before-dispatch) for the required STS actor configuration and initial protocol limits.

To adapt the examples, configure an exchange-capable OAuth authorization server with separate agent and gateway clients, map gateway audiences/scopes to backend resources/scopes, and replace the fixtures with your actual A2A agent and MCP servers. Configure the gateway with the real HTTPS discovery/token endpoints and trusted CA bundle. The STS and backend must agree on subject/actor claims and enforce their own authorization policies; an OIDC login provider alone does not establish token-exchange support.

These routes are managed through `kong.yml`. The current control-plane publisher generates MCP-only configurations and would remove the manually configured exchange policies and A2A route. Do not publish over this example using the registry UI.
