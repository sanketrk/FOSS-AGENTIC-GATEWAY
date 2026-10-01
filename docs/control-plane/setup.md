# Open Agentic Gateway control plane

The Open Agentic Gateway administrator service provides a persistent MCP server registry, authenticated JSON API, and browser UI. Python and SQLite keep it small; PyJWT and cryptography verify signed tokens. Agents connect to the gateway, not this administrator service. The current UI/API manages MCP registrations; A2A and exchange policies are configured separately in gateway deployment files.

## OIDC login

OIDC is the default authentication mode. Configure a trusted issuer, register a client using authorization code flow, and register exact callback/logout URLs. The application discovers the authorization, token, JWKS, and optional logout endpoints from OIDC metadata. Issuers with paths and endpoints on different HTTPS hosts are supported. See [OIDC Discovery](https://openid.net/specs/openid-connect-discovery-1_0.html).

The login flow uses PKCE S256, state bound to an HttpOnly cookie, nonce, and exact issuer/client-audience checks. Signing algorithms are explicitly allowed; symmetric algorithms and unsigned tokens are rejected. Token endpoint authentication supports `client_secret_basic` (default), `client_secret_post`, and public clients with `none` plus PKCE. The callback validates a returned `iss` parameter, requiring it when the issuer advertises support. TLS certificate verification stays enabled. Token endpoint redirects are rejected to prevent forwarding client credentials.

Administrator authorization is a deployment policy, **not a standard OIDC role claim**. Configure an exact claim/value that your issuer controls. By default `/roles` in the verified ID token must contain `agentic-admin`. RFC 6901 JSON pointers support nested claims such as `/realm_access/roles` or namespaced claims such as `/https:~1~1claims.example.com~1roles`. The claim must be a matching string or contain the configured value in an array. Requested scopes alone do not grant administrator access. Ensure users cannot assign themselves the authorization claim through editable profile data.

The default ID-token policy does not require JWT access tokens or an API audience: opaque access tokens from the token endpoint are supported for interactive login. An alternative `access_token` claim policy verifies a signed JWT against `OIDC_API_AUDIENCE` and binds its subject to the ID token. Bearer API automation also requires this configured audience and a JWT carrying the administrator claim. Opaque API tokens/introspection, encrypted ID tokens, client assertions, and refresh-token flows are outside the implemented profile.

The browser receives only an opaque session cookie during login, not access or refresh tokens. The ID token is retained server-side as an OIDC logout hint, and is supplied to the discovered logout endpoint when signing out. It is never included in `/api/session`. Sessions live only in this single control-plane process, expire after 15 minutes by default (bounded by token expiry), and are cleared on restart. HTTPS uses HttpOnly, Secure, SameSite=Lax cookies; loopback HTTP is supported for development. Mutations require the configured Origin and CSRF token. Changes to upstream administrator claims take effect on reauthentication or session expiry, not immediately. Audit events record the verified subject.

Logout always clears the local session. When discovery advertises `end_session_endpoint`, the browser uses OIDC RP-Initiated Logout with `id_token_hint`, `client_id`, and `post_logout_redirect_uri`; a provider may ask the user to confirm logout. Without that endpoint, logout is local only. No proprietary logout URL is constructed. See [RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html).

## Local setup

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r apps/control-plane/requirements.txt
test -f apps/control-plane/.env || cp apps/control-plane/.env.example apps/control-plane/.env
```

Edit the ignored `.env` with your issuer/client settings and administrator claim policy. Register `http://127.0.0.1:8080/auth/callback` as the client callback and `http://127.0.0.1:8080/` as the post-logout redirect.

For Auth0, complete the [provider setup guide](../providers/auth0.md#tenant-setup), including its API audience, the web application's **User-delegated Access** grant, and your login user's administrator role. The generic default `/roles` policy differs from that guide's `/permissions` access-token policy; use the complete settings from the provider guide. Start the service:

```sh
set -a
. apps/control-plane/.env
set +a
python3 apps/control-plane/app.py
```

Open http://127.0.0.1:8080 and sign in with your identity provider. Remote deployments must use an HTTPS public URL. The configured URL determines callback URLs and Secure cookie behavior; untrusted forwarded headers do not override it.

Keep the process running and restart it after editing environment settings. `CONTROL_PLANE_PUBLIC_URL` identifies this browser UI; `GATEWAY_PUBLIC_URL` identifies the separate MCP API origin, without `/mcp`. A local control plane can manage registrations for an OpenShift-hosted gateway. Registry publication to the cluster requires the publisher configuration described below; setting the gateway URL alone does not enable it. For callback/provider failures, see [login troubleshooting](../providers/auth0.md#troubleshooting-login).

| Setting | Purpose |
| --- | --- |
| `OIDC_ISSUER` | Exact HTTPS issuer, including any path/trailing slash |
| `OIDC_DISCOVERY_URL` | Optional explicit metadata URL; defaults to issuer plus `/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | Registered client credentials; secret stays server-side |
| `OIDC_TOKEN_AUTH_METHOD` | `client_secret_basic`, `client_secret_post`, or `none` |
| `OIDC_SCOPES` | Space-separated scopes; must include `openid` |
| `OIDC_SIGNING_ALGORITHMS` | Comma-separated allowed asymmetric JWT algorithms; defaults to `RS256` |
| `OIDC_ADMIN_CLAIM`, `OIDC_ADMIN_VALUE` | Trusted claim JSON pointer and required administrator value |
| `OIDC_ADMIN_CLAIM_SOURCE` | `id_token` (default) or verified JWT `access_token` |
| `OIDC_API_AUDIENCE` | Optional expected audience for JWT API tokens; required for access-token claim policy |
| `OIDC_RESOURCE` | Optional standard RFC 8707 resource sent in both authorization and token requests |
| `OIDC_AUTHORIZATION_PARAMS` | Optional JSON map of provider extension parameters; cannot override reserved OIDC fields |
| `CONTROL_PLANE_PUBLIC_URL` | Public control-plane origin, distinct from MCP resources |
| `GATEWAY_PUBLIC_URL` | Public gateway origin used for registered MCP resources |
| `PUBLISH_NAMESPACE` | Optional Kubernetes namespace enabling publication |
| `PUBLISH_CONFIGMAP`, `PUBLISH_DEPLOYMENT` | Publication targets; default to `open-agentic-gateway-kong` and `open-agentic-gateway` |

[Auth0](../providers/auth0.md) is an optional provider configuration example using this same generic client. It is not a built-in dependency. For explicit local development only, `AUTH_MODE=token` retains the shared-token mode with a strong `CONTROL_PLANE_TOKEN`; there is no automatic fallback in OIDC mode.

## Server registration and publication

Register the complete backend URL (including its MCP path), public server ID, trusted issuer/discovery URL, and required scopes. The token audience is the server's public MCP resource URI, for example `https://mcp.company.com/mcp/server-a`, consistent with resource-bound access tokens. The UI derives it from the gateway origin and server ID. Legacy opaque audience aliases from earlier revisions must be replaced in the authorization server's configuration and newly issued tokens. Generated configurations use resource-URI audiences.

Registration is a draft change. Review the complete configuration before publication. Download `kong.json` for a manual deployment, or publish with the optional Kubernetes integration. Generated config contains only registered named servers and replaces static sample routes, including `/mcp` and root metadata. Each registered resource has its own protected-resource metadata endpoint. There is no open self-registration endpoint. Backend tool execution, custom parameter header validation, and legacy sessions remain upstream responsibilities.

## API

All `/api/*` routes require a browser session or a signed JWT bearer access token with the configured API audience and administrator claim. Session mutations require `X-CSRF-Token` returned by `/api/session`; mutations use `application/json`. No CORS policy is enabled.

| Method | Path | Operation |
| --- | --- | --- |
| GET | `/api/session` | Authenticated identity and session CSRF token |
| GET | `/api/servers` | List draft servers and gateway URL |
| POST | `/api/servers` | Register a server; duplicate IDs return 409 |
| PUT | `/api/servers/{id}` | Replace a policy; ID cannot change |
| DELETE | `/api/servers/{id}` | Remove a draft server |
| GET | `/api/preview` | Complete config and revision hash |
| GET | `/api/status` | Publication request state and last 50 audit events |
| POST | `/api/publish` | Publish `{ "revision": "<reviewed hash>" }`; stale revisions return 409 |

```json
{
  "id": "server-a",
  "name": "My MCP server",
  "upstream_url": "http://server-a.internal:3000/mcp",
  "issuer": "https://identity.company.com/realms/enterprise",
  "discovery_url": "https://identity.company.com/realms/enterprise/.well-known/openid-configuration",
  "audience": "https://mcp.company.com/mcp/server-a",
  "required_scopes": ["mcp:server-a:access"],
  "allowed_origins": [],
  "legacy_enabled": true
}
```

## Optional OpenShift deployment

Build/push the images and set their references, `GATEWAY_PUBLIC_URL`, `CONTROL_PLANE_PUBLIC_URL`, and the administrator policy in the manifests. Register the corresponding HTTPS callback and post-logout redirect at your issuer. Load credentials into a Secret:

```sh
docker build -t open-agentic-control-plane:latest apps/control-plane
oc create secret generic open-agentic-control-plane-oidc \
  --from-literal=issuer="$OIDC_ISSUER" \
  --from-literal=client-id="$OIDC_CLIENT_ID" \
  --from-literal=client-secret="$OIDC_CLIENT_SECRET"
oc apply -k deploy/openshift/control-plane
```

The control plane runs as one replica with a persistent SQLite volume and Recreate strategy. OpenShift supplies its runtime UID. Its dedicated service account can get/patch only the named gateway ConfigMap and Deployment. Kong pods have no service-account token or Admin API listener. The overlay creates no public Route; expose the UI through your private administrator TLS ingress.

Publication updates the ConfigMap and requests a rolling restart through a Deployment annotation. Success means **rollout requested**, not completed. Verify `oc rollout status deployment/open-agentic-gateway` and authenticated calls before treating a revision as live. Replicas may temporarily run different policies. The two writes are not atomic: after partial failure, inspect cluster state and retry the reviewed revision. Publishing a reviewed empty registry removes all gateway routes. Do not deploy multiple SQLite writers or concurrently manage this ConfigMap with static manifests/GitOps. Back up the database and exported configuration. Automatic rollback and live backend/issuer availability checks are not implemented.

## Verification

```sh
PYTHONPATH=apps/control-plane python3 -m unittest discover -s tests/control-plane -v
```

CI verifies generic issuer discovery, token authentication methods, signed-token rejection, authorization claims, opaque browser access tokens, CSRF/session security, container hardening, generated Kong configuration, and routing. These checks cover the documented profile; they are not OpenID certification or a complete MCP server conformance suite. See [the interoperability profile](../protocols/interoperability.md).
