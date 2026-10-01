# FOSS-AGENTIC-GATEWAY Python SDK

This package provides gateway-only MCP/A2A clients and a backend JWT verifier that requires the configured token-exchange identity policy. The banking examples use it in both agents and both MCP servers.

```sh
python -m pip install ./sdk/python
```

## Caller: use the gateway

```python
from foss_agentic_gateway import Endpoint, GatewayClient, OAuthClientCredentials

provider = OAuthClientCredentials(
    token_endpoint="https://issuer.example.com/token",
    client_id="banking-orchestrator",
    client_secret=agent_secret,          # Loaded from your secret store.
)
gateway = GatewayClient(
    gateway_url="https://agents.example.com",
    token_provider=provider,
)
accounts = Endpoint(
    path="/mcp/accounts",
    audience="https://agents.example.com/mcp/accounts",
    scopes=("accounts:read",),
    protocol="mcp",
)
result = gateway.call_tool(accounts, "get_account_summary", {"account_id": "DEMO-001"})

review = Endpoint(
    path="/a2a/transaction-review",
    audience="https://agents.example.com/a2a/transaction-review",
    scopes=("a2a:review",),
    protocol="a2a",
)
response = gateway.send_message(review, "Summarize DEMO-TX-003")
```

The client obtains an agent token for each gateway audience, verifies HTTPS certificates, refuses redirects, and builds RPC URLs only relative to the configured gateway origin. It rejects absolute backend URLs in `Endpoint.path` and HTTP gateway origins. Supply `ca_file` to both objects for a private CA; otherwise they use system trust. There is no TLS-bypass option.

`OAuthClientCredentials` uses standard client credentials with an RFC 8707 `resource` parameter. Alternative grants can be supplied through a token provider implementing `get_token(audience, scopes)`; the SDK does not contain vendor-specific audience extensions.

The client has only agent credentials. It does not call the exchange grant or hold backend tokens. Configure the gateway's `token-exchange` plugin on each authenticated route; it is the broker that exchanges the caller token using its own STS credentials.

## Backend: mandate the exchange policy before dispatch

```python
from foss_agentic_gateway import ExchangeTokenVerifier, AuthenticationError

verifier = ExchangeTokenVerifier(
    issuer="https://issuer.example.com/",
    audience="urn:bank:backend:accounts",
    scopes=("accounts:summary",),
    gateway_actor="banking-gateway",
    subjects=("banking-orchestrator",),  # Optional caller allowlist.
    jwks=public_signing_keys,           # Trusted public JWKS from your deployment.
)

# Your HTTP middleware runs this before executing an agent method or MCP tool.
try:
    claims = verifier.verify(request.headers.get("Authorization"))
except AuthenticationError:
    return unauthorized_response       # HTTP 401 + WWW-Authenticate: Bearer.

return dispatch_application_request(claims)
```

The verifier requires a signed JWT with the trusted issuer, backend audience, expiry, issued-at time, required backend scopes, and **`act.sub` equal to the configured gateway actor**. It rejects original gateway tokens, tokens for other backends, missing or incorrect actors, expired tokens, and invalid signatures. These checks use explicit exceptions and remain active with Python optimization enabled. There is no `require_exchange=False` switch.

The STS must issue the signed actor claim under its configured exchange policy. [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693#section-4.1) defines `act`; it does not require all exchanged tokens to include it. This SDK deliberately requires it for its exchange-only backend profile. An OAuth grant name is not cryptographic evidence encoded in a token. Enforcement trusts the issuer's signed audience/actor/scope claims and its issuance policy, not a caller-provided header or response receipt. Network access restrictions still belong to your deployment.

The banking fixture's `token_use` and `verified_upstream_identity` receipt are diagnostic data; neither is an SDK authorization mechanism. The SDK does not depend on Auth0 or any particular issuer.

## Current profile

Version `0.1.0` provides synchronous A2A 1.0 `SendMessage`/public card reads and MCP 2025-11-25 stateless initialization, metadata checks, tool discovery and calls, with JSON responses. It is a small gateway SDK, not a replacement for a complete protocol SDK. SSE, resumable or stateful MCP sessions, A2A task lifecycle, async clients, and automatic token/key refresh are outside this initial profile. The backend verifier uses an explicitly supplied public signing JWKS with distinct `kid` values; reload trusted keys when your issuer rotates them. Opaque backend tokens need a different verifier and are not supported by this JWT guard.

See the [four banking components](../../examples/banking/README.md) for runnable Docker images and complete middleware integration.
