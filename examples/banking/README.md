# Banking examples: agents, MCP, and token exchange

Run a banking orchestrator through FOSS-AGENTIC-GATEWAY in three ways:

1. **Agent → agent:** request a transaction summary from an A2A transaction-review agent.
2. **Agent → one MCP server:** call `get_account_summary` on the account service.
3. **Agent → multiple MCP servers:** combine an account summary with `list_recent_transactions` from a separate service.

Every authenticated route uses the generic `token-exchange` plugin. The caller gets a gateway access token; the gateway validates it and requests a different backend token using the [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) exchange grant. The caller never receives the exchanged token or the gateway's STS credentials.

All account and transaction data is synthetic. The orchestrator and review agent are deterministic Python examples, with no LLM dependency. They make no payments and implement only the protocol methods used in these examples.

## What runs

```text
Banking orchestrator ── HTTPS ── Gateway ── backend token ── Transaction-review agent
                                   │
                                   ├── backend token ──── Accounts MCP server
                                   ├── backend token ──── Transactions MCP server
                                   │
                                   └── HTTPS / token exchange ── Local issuer + STS fixture
```

The local OAuth issuer signs short-lived gateway JWTs and implements client credentials, discovery/JWKS, and the token-exchange requests needed here. It is a test fixture, not a complete OIDC provider. TLS uses a generated demo CA that containers explicitly trust; verification stays enabled. No external IdP or preexisting credentials are required.

| Destination | Gateway audience | Gateway scope | Exchanged backend audience | Backend scope |
| --- | --- | --- | --- | --- |
| Review agent | `https://gateway:8443/a2a/transaction-review` | `a2a:review` | `urn:bank:backend:transaction-review` | `review:execute` |
| Accounts MCP | `https://gateway:8443/mcp/accounts` | `accounts:read` | `urn:bank:backend:accounts` | `accounts:summary` |
| Transactions MCP | `https://gateway:8443/mcp/transactions` | `transactions:read` | `urn:bank:backend:transactions` | `transactions:recent` |

The STS checks the gateway client, the subject token's issuer/signature/expiry/audience/scope, and the requested resource/scope against fixed policy. Its backend token preserves `sub=banking-orchestrator`, records `act.sub=banking-gateway`, and changes the audience and scope for the destination. Backends independently verify that token. The original gateway token is rejected by every backend.

## Start the demo

Use Docker with Compose v2. Run these commands from the repository root:

```sh
docker compose -f examples/banking/compose.yml --profile tools build
docker compose -f examples/banking/compose.yml run --rm prepare
docker compose -f examples/banking/compose.yml up -d --wait issuer review-agent accounts transactions gateway
```

`prepare` generates disposable keys, certificates, and random client secrets into separate named volumes. Only the issuer receives its signing key. The gateway receives its own exchange credential, the caller receives only its agent credential, and backends receive public verification keys. Initialization runs without a network. Stop existing demo containers before running `prepare` again, since it rotates these credentials.

The caller runs on the Compose `agents` network and uses `https://gateway:8443`; that DNS name is internal to Compose. There is no host DNS configuration to perform. The gateway's HTTPS port is also bound to `127.0.0.1:8443` for local inspection. The CA is not installed into the host trust store.

## 1. An agent calls another agent

```sh
docker compose -f examples/banking/compose.yml run --rm client client.py a2a
```

The orchestrator fetches the public card at `/cards/transaction-review`, acquires a JWT for the gateway A2A audience, and sends `SendMessage` with `A2A-Version: 1.0`. The gateway exchanges that JWT for the review agent's resource and calls its private `/rpc` endpoint. The responder returns an A2A `SendMessageResponse.message` summarizing synthetic merchant purchase `DEMO-TX-003`.

The example uses the [A2A 1.0 JSON-RPC HTTP binding](https://a2a-protocol.org/latest/specification/). Its card advertises the public gateway interface and Bearer JWT security. The card is anonymous and skips exchange; `SendMessage` is authenticated and exchanged. Streaming, task persistence, and push notifications are not part of this fixture.

## 2. An agent calls one MCP server

```sh
docker compose -f examples/banking/compose.yml run --rm client client.py mcp accounts
```

The orchestrator reads OAuth protected-resource metadata, obtains an account-specific gateway token, and performs `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. It requests `get_account_summary` for synthetic account `DEMO-001`, returning its INR balance and status.

The MCP fixtures use the gateway's [2025-11-25 Streamable HTTP compatibility profile](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), with POST JSON responses and a stateless server. Requests send `Accept: application/json, text/event-stream`; notifications return HTTP 202. These examples do not exercise the newer POST-only profile or SSE resumption.

## 3. An agent calls multiple MCP servers

```sh
docker compose -f examples/banking/compose.yml run --rm client client.py mcp accounts transactions
```

The same orchestrator obtains a separate gateway token for each MCP audience, opens each server independently, and combines the account summary with three synthetic recent transactions. Each request is exchanged for that server's backend audience and scopes. An account token cannot be reused against the transactions endpoint.

All successful responses include a demonstration `verified_upstream_identity` receipt. For accounts it looks like:

```json
{
  "subject": "banking-orchestrator",
  "audience": "urn:bank:backend:accounts",
  "scope": "accounts:summary",
  "actor": "banking-gateway",
  "token_use": "upstream"
}
```

The receipt is fixture output after backend JWT verification, not an identity header inserted by the gateway. Bearer tokens and secrets are never printed.

## Verify and stop

Run all scenarios plus negative checks:

```sh
docker compose -f examples/banking/compose.yml run --rm client client.py all
```

The client asserts that exchanged identities match each backend, and that wrong gateway audiences, missing tokens, and attempts to exchange with caller credentials are rejected. Fixture tests also verify that backends reject original gateway tokens and tokens for a different backend, and that the STS rejects expired subjects and expanded scopes. CI runs both these tests and this real Kong/Compose demo.

The backend services have no published ports and join only the internal `backends` network; the caller joins only `agents`. Backend HTTP is isolated to this local demo network. Use your deployment's authenticated encrypted backend transport and network controls when adapting the topology.

```sh
docker compose -f examples/banking/compose.yml down -v
```

This removes only the demo's containers, networks, and credential volumes.

## Files and adaptation

| File | Purpose |
| --- | --- |
| [kong.yml](kong.yml) | One A2A and two MCP routes, each with its own exchange policy |
| [compose.yml](compose.yml) | Local gateway, issuer, backends, caller, isolated networks, and credential mounts |
| [client.py](client.py) | Agent-to-agent and agent-to-MCP request sequences |
| [serve.py](serve.py) | Bounded synthetic issuer/STS, review agent, and MCP responders |
| [common.py](common.py) | Example audience/scope policies, JWT checks, and synthetic banking data |
| [prepare.py](prepare.py) | Disposable CA, service certificates, keys, and separate client credentials |

To adapt the examples, configure an exchange-capable OAuth authorization server with separate agent and gateway clients, map gateway audiences/scopes to backend resources/scopes, and replace the fixtures with your actual A2A agent and MCP servers. Configure the gateway with the real HTTPS discovery/token endpoints and trusted CA bundle. The STS and backend must agree on subject/actor claims and enforce their own authorization policies; an OIDC login provider alone does not establish token-exchange support.

These routes are managed through `kong.yml`. The current control-plane publisher generates MCP-only configurations and would remove the manually configured exchange policies and A2A route. Do not publish over this example using the registry UI.
