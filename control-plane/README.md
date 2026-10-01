# Lightweight control plane

A separate administrator service provides a persistent MCP server registry, authenticated JSON API, and browser UI. Python and SQLite keep it small; PyJWT and cryptography verify OIDC token signatures. Agents continue connecting to Kong; they do not connect to this service.

## Auth0 OIDC login

OIDC is the default authentication mode. Follow the [Auth0 setup guide](AUTH0.md) to create a Regular Web Application and a dedicated control-plane API with the `control-plane:admin` permission. An authenticated account without that assigned permission receives 403.

The browser signs in through Auth0; authorization codes are exchanged on the backend using PKCE and the confidential client secret. ID and access tokens are verified with issuer, audience, RS256 signature, expiry, nonce, and subject checks. No OAuth tokens are returned to the browser. HTTPS sessions use HttpOnly, Secure, SameSite=Lax cookies; loopback HTTP is supported for development. Mutations require both the configured Origin and a session CSRF token.

Sessions live only in this single control-plane process, expire after 15 minutes by default (bounded by token expiry), and are cleared on restart. Role revocation takes effect on reauthentication or session expiry, not immediately. Logout deletes the local session and redirects through Auth0 logout. There is no refresh-token storage or automatic token renewal. Audit events record the verified Auth0 subject.

API automation can use an Auth0 API access token with the same audience and `control-plane:admin` permission. OIDC mode never falls back to the old shared administrator token. For explicit local development only, `AUTH_MODE=token` retains the original token mode; install the requirements, set `CONTROL_PLANE_TOKEN` to a strong secret, and start the app. Browser tokens remain in page memory in that mode.

Register a server with its complete backend URL (including its MCP endpoint path), public ID, trusted issuer and discovery URL, unique audience, and required scopes. HTTPS is required for public gateway/identity-provider URLs; HTTP backends are supported for internal networks. Browser origins are optional and denied unless listed. Token forwarding remains disabled; verification uses RS256 with TLS verification enabled.

Registration is a draft change. **Review configuration** shows a complete snapshot. Download `kong.json` for a manual deployment, or publish with the optional Kubernetes integration. JSON is valid declarative YAML for Kong. The generated configuration contains only registered named servers; it replaces static sample routes, including `/mcp` and its root metadata. Each registered resource retains its own metadata endpoint. There is no open self-registration endpoint.

## API

All `/api/*` routes require an authenticated OIDC session or `Authorization: Bearer <Auth0 API access token>` with the administrator permission. Session mutations also require `X-CSRF-Token` returned by `/api/session`. Mutations require `Content-Type: application/json`. No CORS policy is enabled.

| Method | Path | Operation |
| --- | --- | --- |
| GET | `/api/session` | Get authenticated identity and session CSRF token |
| GET | `/api/servers` | List registered draft servers and gateway URL |
| POST | `/api/servers` | Register a server; duplicate IDs return 409 |
| PUT | `/api/servers/{id}` | Replace a server policy; ID cannot change |
| DELETE | `/api/servers/{id}` | Remove a draft server |
| GET | `/api/preview` | Get complete Kong config and revision hash |
| GET | `/api/status` | Publication request state and last 50 audit events |
| POST | `/api/publish` | Publish `{ "revision": "<reviewed hash>" }`; stale revisions return 409 |

Example registration body:

```json
{
  "id": "server-a",
  "name": "My MCP server",
  "upstream_url": "http://server-a.internal:3000/mcp",
  "issuer": "https://identity.company.com/",
  "discovery_url": "https://identity.company.com/.well-known/openid-configuration",
  "audience": "mcp-server-a",
  "required_scopes": ["mcp:server-a:access"],
  "allowed_origins": [],
  "legacy_enabled": true
}
```

## Optional OpenShift deployment

Build and push both gateway and control-plane images, then set the image references and `GATEWAY_PUBLIC_URL` / `CONTROL_PLANE_PUBLIC_URL` in the manifests. The base deployment stays unchanged; this overlay adds the control plane.

```sh
docker build -t mcp-control-plane:latest control-plane
oc create secret generic mcp-control-plane-auth0 \
  --from-literal=domain="$AUTH0_DOMAIN" \
  --from-literal=client-id="$AUTH0_CLIENT_ID" \
  --from-literal=client-secret="$AUTH0_CLIENT_SECRET"
oc apply -k deploy/control-plane
oc port-forward service/mcp-control-plane 8080:8080
```

The control plane runs as one replica with a persistent SQLite volume and Recreate strategy. OpenShift supplies its runtime UID. Its dedicated service account can get/patch only the named gateway ConfigMap and Deployment in its namespace. Kong pods still do not mount service-account tokens and their Admin API remains disabled. The overlay creates no public Route; expose the UI through your administrator ingress if needed.

Publishing patches `kong.yml` in the ConfigMap and the gateway Deployment's pod-template annotation to request a rolling restart. New pods load that persisted configuration. A successful API response means **rollout requested**, not rollout completed. Verify `oc rollout status deployment/mcp-gateway` and authenticated calls before considering the revision live. This rollout strategy deliberately keeps the Kong Admin API disabled; see [Kong DB-less configuration](https://developer.konghq.com/gateway/db-less-mode/).

Replicas can temporarily use different policies during a rolling update; removal or revocation is not immediate across all pods. ConfigMap and Deployment writes are not atomic. If publication fails between them, the API reports failure and records an event; inspect cluster state and retry the same reviewed revision. Requests serialize within the single control-plane process. Do not deploy multiple writable control-plane instances against this SQLite database.

Do not manage the same ConfigMap concurrently with GitOps or repeated static `oc apply` operations: those can overwrite published registrations. Treat the registry as the owner once enabled. Export and back up the configuration and persistent database. Roll back by restoring a known-good draft and publishing it; version history/automatic rollback are not yet implemented. Registry changes do not validate backend availability or perform live IdP discovery.

## Verification

```sh
python3 -m pip install -r control-plane/requirements.txt
python3 -m unittest discover -s control-plane -v
```

CI also builds the control-plane container and runs its tests as an arbitrary UID with a read-only root filesystem, renders the optional deployment, and parses a generated registry configuration using Kong.
