# FOSS-AGENTIC-GATEWAY: A2A plugin

The optional `a2a` Kong OSS plugin proxies the **JSON-RPC and A2A 1.0 HTTP+JSON/REST bindings** of the [Agent2Agent protocol](https://a2a-protocol.org/latest/specification/), originally introduced by Google. It remains vendor neutral. It supports transport profiles `1.0` and legacy `0.3`; it is not a complete A2A agent, protocol translator, or certification claim. gRPC is not implemented.

## Configuration

[examples/gateway/a2a.yml](../../examples/gateway/a2a.yml) contains an isolated example. Merge its service into your gateway configuration after replacing the upstream, public audience, issuer/discovery URL, and scope policy. Keep `retries: 0` to avoid repeating agent operations and both buffering flags false for SSE. The image and deployment enable the plugin; default MCP routes do not activate it. Do not attach `mcp` to an A2A route: these protocols have different transport rules.

The example maps `/a2a/agent-a` to the backend `/rpc` endpoint. GET `/.well-known/agent-card.json` is proxied to the configured `upstream_card_path` on the same backend. Each additional agent needs a distinct RPC endpoint and card URL, or its own hostname. Only one agent can own the host-wide well-known card URL. Namespaced card URLs may be used through direct configuration or a catalog.

The upstream must publish its card with the gateway's externally reachable interface URL and correct version, binding, capabilities, and gateway authentication requirements. The gateway passes card content and caching/signature headers through without rewriting them. This preserves signed content but does not verify card signatures or synchronize the card with gateway policy. For 1.0, advertise `supportedInterfaces` entries with `protocolBinding: JSONRPC`, `protocolVersion`, and public `url`; legacy 0.3 uses its own card format. Advertise only versions actually supported by that upstream.

## Authentication and transport

RPC calls and authenticated extended-card operations require JWT access tokens. Configure exact trusted issuer/discovery pairs, audience, asymmetric signature algorithms, and scopes. HTTPS discovery verification is always enabled. The agent-specific audience prevents accepting a token for another agent. Public card GET defaults to anonymous access and strips any bearer header; set `public_card: false` for sensitive cards. Authentication remains mandatory for RPC methods such as `GetExtendedAgentCard` and the corresponding legacy operation.

The plugin validates POST, `application/json`, the JSON-RPC envelope, and version selection. Missing or empty `A2A-Version` means legacy `0.3`; header/query conflicts and repeated values are rejected. Unsupported versions receive JSON-RPC error `-32009`. Unknown methods are passed to the upstream for its version-specific method handling, rather than being rejected by a gateway allowlist. Task/message schemas, extension requirements, tenant routing, task ownership, cancellation, push notification safety, and streaming response semantics belong to the upstream. A2A version/extension headers, tenant parameters, and response bodies pass through unchanged. There is no automatic method or version conversion.

Bearer tokens are stripped by default. Independently isolate and authenticate backend access. Enable `forward_bearer_token` only when that backend is within the same protected-resource trust boundary and validates the same audience; it does not enable exchange. Attach the generic [`token-exchange`](../plugins/token-exchange.md) plugin to the same route to obtain a separate backend token after A2A authentication. Set its `gateway_audience` to this plugin's `audience`. Anonymous public-card requests skip exchange and send no Authorization header; protected cards and RPC calls can exchange tokens. Opaque incoming tokens, API keys, mTLS identities, and other A2A security schemes are outside this JWT profile. The card must describe the security scheme actually enforced here.

## Deployment and verification

Merge the service into your environment's Kong ConfigMap, update the image to a build containing the plugin, ensure the `KONG_PLUGINS` list includes `a2a`, and roll out. Expose HTTPS with adequate streaming timeouts. No example agent is automatically deployed.

```sh
curl https://agents.example.com/.well-known/agent-card.json
curl -i https://agents.example.com/a2a/agent-a \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  --data '{"jsonrpc":"2.0","id":1,"method":"GetTask","params":{"id":"task-example"}}'
```

The second request should receive 401 without credentials. With an appropriately scoped access token, a real upstream interprets the request and returns its task or protocol error. Streaming calls require that upstream's streaming capability and version-specific method. Tests exercise gateway validation and configuration loading; complete SDK/agent interoperability still requires a live upstream and issuer.

The current control-plane registry manages MCP servers only. Its publication regenerates MCP routes and removes manually merged A2A routes; manage combined configurations through deployment tooling until registry support is extended. The local cluster's existing deployment is unchanged by adding this optional plugin to source.

## A2A 1.0 REST binding

Set `rest_path` to a public base path without a trailing slash (for example `/a2a/agent-a/rest`) and add `~/a2a/agent-a/rest/` to the Kong route paths. The plugin strips this public prefix and sends the operation to `upstream_rest_path` (default empty). Thus `POST /a2a/agent-a/rest/message:send` reaches `POST /message:send` on the agent, independently of the service's JSON-RPC `/rpc` path. Query parameters remain unchanged.

Advertise a second `supportedInterfaces` entry with `protocolBinding: HTTP+JSON`, `protocolVersion: "1.0"`, and the public REST base URL. Both bindings use the same agent audience, required scopes, and optional token-exchange plugin. REST requests require `A2A-Version: 1.0` in the header; the legacy 0.3 binding is JSON-RPC only in this gateway profile.

The gateway recognizes message send/stream, task get/list/cancel/subscribe, push-notification configuration create/get/list/delete, and authenticated `/extendedAgentCard` routes. It validates their HTTP methods and JSON objects on POST, authenticates every operation, then proxies payloads and responses without converting bindings. REST validation errors use an HTTP status and `error` status object; A2A-specific errors carry `google.rpc.ErrorInfo`. Encoded identifiers and dot paths are rejected by this initial route profile. Tenant-prefixed REST paths are not supported. Upstreams own operation semantics, task ownership and capabilities; SSE stays unbuffered. The banking example implements synchronous SendMessage only, and the Python SDK supports that REST operation only.

```python
endpoint = Endpoint(path="/a2a/agent-a/rest", audience="https://agents.example.com/a2a/agent-a",
                    scopes=("a2a:access",), protocol="a2a", binding="HTTP+JSON")
result = gateway.send_message(endpoint, "Review a synthetic transaction")
```

Binding reference: [A2A 1.0 HTTP+JSON/REST specification](https://a2a-protocol.org/v1.0.0/specification/#11-httpjsonrest-protocol-binding).
