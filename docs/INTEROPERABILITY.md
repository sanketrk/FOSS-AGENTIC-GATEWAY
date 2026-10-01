# Protocol interoperability profile

This project is a transport-aware MCP gateway and a separate administrator control plane. Protocols are provider-neutral; Kong is the chosen proxy implementation, and OpenShift is an optional deployment target. Identity-provider selection does not require changing code. The two authentication roles are distinct:

- **Control plane:** an OIDC relying party authenticating administrators through authorization code flow.
- **MCP gateway:** an OAuth protected resource verifying agents' access tokens. OIDC ID tokens are not MCP access tokens.

## OIDC profile

Implemented: issuer-bound OIDC discovery, code flow with PKCE S256, state and nonce, asymmetric JWT verification, client audience/authorized-party validation, OAuth response issuer validation, standard client-secret authentication or public-client PKCE, opaque browser access tokens when authorizing from ID-token claims, and discovered RP-Initiated Logout with local fallback.

Administrator roles/permissions are **not standardized by OIDC**. The trusted claim source, RFC 6901 pointer, and required value are configurable. Provider-specific authorization parameters are opt-in extensions. Default login requires only `openid profile email` scopes and contains no vendor `audience` parameter. If an API resource is requested, `OIDC_RESOURCE` uses RFC 8707 and is included in both authorization and token requests.

Supported signature algorithms are RS256/384/512, ES256/384, and EdDSA when explicitly configured. HS algorithms, encrypted ID tokens, dynamic OIDC client registration, refresh-token storage, token introspection, client assertions, and back-channel logout are not implemented. API bearer authentication and access-token administrator claim policies require signed JWTs. The implementation is tested against protocol fixtures, not OpenID-certified or verified against every issuer.

References: [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html), [Discovery](https://openid.net/specs/openid-connect-discovery-1_0.html), [RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html), [PKCE](https://www.rfc-editor.org/rfc/rfc7636), [OAuth response issuer](https://www.rfc-editor.org/rfc/rfc9207).

## MCP transport and authorization profile

The gateway supports Streamable HTTP revision `2026-07-28`, with configurable compatibility for `2025-11-25` and `2025-03-26`. It is not an MCP server or client implementation. The upstream handles tool schemas, method existence, response semantics, MRTR, subscriptions, cancellation, and session lifecycle for legacy revisions.

The gateway enforces JSON POST envelopes, JSON/SSE Accept negotiation, current request metadata/header agreement, required method/name headers, Origin policy, and OAuth authentication. Current extension notifications are not required to carry request-only method headers. Current requests are stateless; legacy session/resume headers are stripped for them. Legacy GET/DELETE/session routing remains available when configured. Unknown extension methods and unrecognized `Mcp-Param-*` headers pass through; upstream servers validate recognized custom parameter headers against their schemas. Unsupported transport requests get HTTP errors; full MCP error semantics remain the server's responsibility.

Each MCP server is a separate protected resource with its own canonical public HTTPS resource URI, metadata endpoint, expected token audience, and scopes. Default/sample and generated audiences use that resource URI. Clients use protected-resource metadata to select an authorization server and must send the RFC 8707 `resource` parameter in both authorization and token requests. The gateway does not initiate agent authorization, register OAuth clients, or issue tokens. The selected authorization server and agent must implement their portions of the MCP OAuth flow, including supported client registration and mix-up protection. A vendor-specific `audience` parameter alone does not replace MCP's `resource` requirement.

JWT access tokens are verified against explicitly configured issuer/discovery pairs, allowed algorithms, expiry, audience, and required scopes. Opaque MCP access tokens are not supported by this profile. Resource metadata, 401 challenges, and insufficient-scope responses support OAuth discovery and step-up flows. Token forwarding is disabled by default; any opt-in forwarding must remain within the same protected-resource trust boundary and must not forward a token to an unrelated API. Enforce backend network isolation separately.

An optional [RFC 8693 exchange plugin](TOKEN_EXCHANGE.md) obtains separate backend tokens after incoming-token validation. It requires an explicitly trusted STS; issued tokens are validated by the backend.

No universal MCP conformance or provider certification is claimed. Live tests still need a conforming agent, authorization server, and upstream MCP server. Unit/runtime tests validate this gateway's implemented boundaries.

References: [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http), [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [resource indicators](https://www.rfc-editor.org/rfc/rfc8707), [protected-resource metadata](https://www.rfc-editor.org/rfc/rfc9728), [bearer tokens](https://www.rfc-editor.org/rfc/rfc6750).

## Migration from provider-specific configuration

Replace `AUTH0_*` variables with generic `OIDC_*` settings as shown in the optional provider example. Replace the old Secret with `mcp-control-plane-oidc`, containing `issuer`, `client-id`, and `client-secret`. Configure a trusted administrator claim explicitly; OIDC login alone never authorizes registry administration. Replace opaque sample audience aliases with each server's public resource URI in the IdP/API configuration and issue fresh tokens before publishing the revised gateway configuration. Review the generated snapshot and verify rollout and authenticated requests.
