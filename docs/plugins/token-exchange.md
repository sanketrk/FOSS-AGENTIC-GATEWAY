# FOSS-AGENTIC-GATEWAY: token exchange

The `token-exchange` Kong OSS plugin implements outbound [RFC 8693 token exchange](https://www.rfc-editor.org/rfc/rfc8693). Enable it on an MCP or A2A route alongside `mcp` or `a2a`. It is vendor neutral and inactive by default.

The gateway verifies the incoming token's signature, issuer, public resource audience, expiry, and scopes first. The exchange plugin authenticates to a trusted authorization server/STS and requests a separate upstream token. Only the exchanged token reaches the backend. The original token is sent only to the configured STS as the subject token, with no forwarding fallback.

## Downscoping

Use exchange to give each target only the permissions needed for its operation, even when the original user or agent has many enterprise entitlements. For example, the transaction-review route requests only `review:execute` for `urn:bank:backend:transaction-review`. Unrelated permissions must not appear in the issued backend token.

The gateway's per-route `resource` and `scopes` specify the requested backend access. The STS must authorize those permissions against the subject's entitlements and the gateway client's exchange policy. Gateway client authentication alone must not grant permissions the subject lacks. Incoming and backend scope names can differ, so the STS needs an explicit authorized mapping rather than a simple comparison of scope strings.

This implementation requests a fixed scope set per route and rejects a response that explicitly returns a different set. It does not compute a user's entitlement set or choose scopes dynamically for individual methods. Configure separate routes/policies where different operations need different access, and keep operation-level authorization at the backend. Short token lifetimes are also an STS issuance policy.

## Configuration

Add this entry to the same route's `plugins` array:

```yaml
- name: token-exchange
  config:
    token_endpoint: https://sts.example.com/oauth/token
    gateway_audience: https://mcp.example.com/mcp/server-a
    resource: https://backend.example.com/mcp
    client_id: foss-agentic-gateway-exchange
    client_secret_file: /var/run/secrets/token-exchange/client-secret
    client_auth_method: client_secret_basic
    scopes:
      - tools:read
    timeout_ms: 3000
```

`gateway_audience` must exactly match the companion `mcp` or `a2a` plugin's `audience`. For MCP, that audience is the public resource URI. A2A audiences may also be logical identifiers such as `urn:agent:a`. `resource` identifies the upstream protected resource using an absolute URI without a fragment, such as an HTTPS URL or URN. Only the configured HTTPS `token_endpoint` is contacted; the resource identifier is sent to the STS. Destinations and scopes are administrator-controlled, never selected from incoming headers. Keep `forward_bearer_token: false`. Priority 700 runs exchange after gateway authentication at priority 800.

The STS must support the standard token-exchange grant, accept the incoming subject-token issuer, authorize this client for the resource/scopes, and issue tokens the backend can validate. Match `client_secret_basic` or `client_secret_post` to its registration. HTTPS verification is always enabled. An IdP supporting control-plane OIDC login does not establish token-exchange support.

## Shared authentication contract

Both authentication plugins populate `kong.ctx.shared.gateway_authentication` only after successful signature, issuer, expiry, audience, and scope checks:

```lua
{ authenticated = true, access_token = verified_token, audience = configured_audience }
```

The exchange plugin uses this context rather than parsing caller-supplied identity headers. Missing context, invalid authentication state, or a different configured audience fails closed. A trusted authentication plugin can explicitly mark public discovery as `{ authenticated = false, audience = configured_audience }` with no token. The A2A plugin uses this for public Agent Cards; exchange clears Authorization and skips the STS for those requests. Other authentication plugins may implement this contract without depending on MCP or A2A internals.

For an A2A route, use the same exchange configuration with `gateway_audience` matching its A2A audience and `resource` identifying its backend. Keep both protocol plugins off each other's routes. Exchange remains an outbound OAuth capability, independent of JSON-RPC method or transport version.

## Deployment and secrets

The image and deployment enable `bundled,mcp,a2a,token-exchange`; only an explicit route entry activates exchange. Store credentials outside declarative configuration:

```sh
oc create secret generic token-exchange --from-file=client-secret=/path/to/local/client-secret -n foss-agentic-gateway
```

Add a read-only Secret volume to the gateway Deployment and mount it at `/var/run/secrets/token-exchange`. Ensure the arbitrary runtime UID can read the file. Apply route configuration and restart the gateway. The plugin rereads the mounted secret per exchange. Never put credentials in a ConfigMap or repository.

The registry UI/API does not yet model exchange settings. Control-plane publication regenerates routes and removes manual additions. Manage exchange-enabled routes through deployment configuration until registry support is added.

## Response policy and limitations

Requests use the standard exchange grant, access-token subject/requested types, fixed `resource`, and configured scopes. The STS must return HTTP 200, an access-token `issued_token_type`, a Bearer `token_type`, and a safe nonempty token. Optional expiry must be positive; optional returned scopes must exactly match the requested set. Omitted scope means the requested scope; omitted expiry is accepted under RFC 8693.

The trusted STS and backend enforce issued-token audience, delegation, and token semantics. Opaque backend tokens are supported; the plugin does not independently verify issued JWT claims. The backend must validate its token. Incoming tokens remain subject to the gateway's existing signed-JWT policy.

Exchange occurs once per authenticated proxied MCP or A2A request, including legacy MCP stream setup and protected A2A cards. Anonymous public A2A cards skip exchange and carry no upstream Authorization header. There is no cache, retry, redirect following, or refresh-token persistence. Failures block forwarding with a generic 502; missing credentials return 503 and missing authentication context or mismatched audience returns 500. Provider bodies and credentials are not returned to callers. SSE remains unbuffered; token expiry during an established stream remains a backend concern.

Endpoint discovery, actor tokens, client assertions, and proprietary exchange grants are outside this implementation. Tests use protocol fixtures; live interoperability requires an exchange-capable STS and authenticated upstream.
