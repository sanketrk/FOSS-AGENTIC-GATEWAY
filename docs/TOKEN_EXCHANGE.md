# Optional upstream token exchange

The `mcp-token-exchange` Kong OSS plugin implements outbound [RFC 8693 token exchange](https://www.rfc-editor.org/rfc/rfc8693). Enable it on an MCP route alongside `mcp-gateway`. It is vendor neutral and inactive by default.

The gateway verifies the incoming token's signature, issuer, public resource audience, expiry, and scopes first. The exchange plugin authenticates to a trusted authorization server/STS and requests a separate upstream token. Only the exchanged token reaches the backend. The original token is sent only to the configured STS as the subject token, with no forwarding fallback.

## Configuration

Add this entry to the same route's `plugins` array:

```yaml
- name: mcp-token-exchange
  config:
    token_endpoint: https://sts.example.com/oauth/token
    gateway_resource: https://mcp.example.com/mcp/server-a
    resource: https://backend.example.com/mcp
    client_id: mcp-gateway-exchange
    client_secret_file: /var/run/secrets/token-exchange/client-secret
    client_auth_method: client_secret_basic
    scopes:
      - tools:read
    timeout_ms: 3000
```

`gateway_resource` must exactly match the companion gateway plugin's `resource_url`. `resource` identifies the upstream protected resource. Destinations and scopes are administrator-controlled, never selected from incoming headers. Keep `forward_bearer_token: false`. Priority 700 runs exchange after gateway authentication at priority 800.

The STS must support the standard token-exchange grant, accept the incoming subject-token issuer, authorize this client for the resource/scopes, and issue tokens the backend can validate. Match `client_secret_basic` or `client_secret_post` to its registration. HTTPS verification is always enabled. An IdP supporting control-plane OIDC login does not establish token-exchange support.

## Deployment and secrets

The image and deployment enable `bundled,mcp-gateway,mcp-token-exchange`; only an explicit route entry activates exchange. Store credentials outside declarative configuration:

```sh
oc create secret generic mcp-token-exchange --from-file=client-secret=/path/to/local/client-secret -n mcp-gateway
```

Add a read-only Secret volume to the gateway Deployment and mount it at `/var/run/secrets/token-exchange`. Ensure the arbitrary runtime UID can read the file. Apply route configuration and restart the gateway. The plugin rereads the mounted secret per exchange. Never put credentials in a ConfigMap or repository.

The registry UI/API does not yet model exchange settings. Control-plane publication regenerates routes and removes manual additions. Manage exchange-enabled routes through deployment configuration until registry support is added.

## Response policy and limitations

Requests use the standard exchange grant, access-token subject/requested types, fixed `resource`, and configured scopes. The STS must return HTTP 200, an access-token `issued_token_type`, a Bearer `token_type`, and a safe nonempty token. Optional expiry must be positive; optional returned scopes must exactly match the requested set. Omitted scope means the requested scope; omitted expiry is accepted under RFC 8693.

The trusted STS and backend enforce issued-token audience, delegation, and token semantics. Opaque backend tokens are supported; the plugin does not independently verify issued JWT claims. The backend must validate its token. Incoming tokens remain subject to the gateway's existing signed-JWT policy.

Exchange occurs once per authorized proxied request, including legacy stream setup. There is no cache, retry, redirect following, or refresh-token persistence. Failures block forwarding with a generic 502; missing credentials return 503 and mismatched configuration returns 500. Provider bodies and credentials are not returned to callers. SSE remains unbuffered; token expiry during an established stream remains a backend concern.

Endpoint discovery, actor tokens, client assertions, and proprietary exchange grants are outside this implementation. Tests use protocol fixtures; live interoperability requires an exchange-capable STS and authenticated upstream.
