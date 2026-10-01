# Lightweight control plane

A separate administrator service provides a persistent MCP server registry, authenticated JSON API, and browser UI. Python's standard library and SQLite keep it dependency-free. Agents continue connecting to Kong; they do not connect to this service.

## Run locally

From the repository root:

```sh
export CONTROL_PLANE_TOKEN="$(openssl rand -hex 32)"
export GATEWAY_PUBLIC_URL=https://mcp.company.com
export REGISTRY_DATABASE=/tmp/mcp-registry.sqlite3
python3 control-plane/app.py
```

Open http://127.0.0.1:8080 and enter the token from your shell. The browser keeps it only in page memory, not local storage or a cookie. The service defaults to loopback. Keep the token private; everyone holding it has full administrator access. Use a TLS ingress and private administrator network for remote access. This initial version uses one shared admin token, not enterprise SSO or per-user roles. Audit events therefore identify actions, not individual administrators. Rotate the token by changing the environment/Secret and restarting the control plane.

Register a server with its complete backend URL (including its MCP endpoint path), public ID, trusted issuer and discovery URL, unique audience, and required scopes. HTTPS is required for public gateway/identity-provider URLs; HTTP backends are supported for internal networks. Browser origins are optional and denied unless listed. Token forwarding remains disabled; verification uses RS256 with TLS verification enabled.

Registration is a draft change. **Review configuration** shows a complete snapshot. Download `kong.json` for a manual deployment, or publish with the optional Kubernetes integration. JSON is valid declarative YAML for Kong. The generated configuration contains only registered named servers; it replaces static sample routes, including `/mcp` and its root metadata. Each registered resource retains its own metadata endpoint. There is no open self-registration endpoint.

## API

All `/api/*` routes require `Authorization: Bearer <administrator-token>`. Mutations require `Content-Type: application/json`. No CORS policy is enabled.

| Method | Path | Operation |
| --- | --- | --- |
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

Build and push both gateway and control-plane images, then set the image references and `GATEWAY_PUBLIC_URL` in the manifests. The base deployment stays unchanged; this overlay adds the control plane.

```sh
docker build -t mcp-control-plane:latest control-plane
oc create secret generic mcp-control-plane-auth --from-literal=token="$CONTROL_PLANE_TOKEN"
oc apply -k deploy/control-plane
oc port-forward service/mcp-control-plane 8080:8080
```

The control plane runs as one replica with a persistent SQLite volume and Recreate strategy. OpenShift supplies its runtime UID. Its dedicated service account can get/patch only the named gateway ConfigMap and Deployment in its namespace. Kong pods still do not mount service-account tokens and their Admin API remains disabled. The overlay creates no public Route; expose the UI through your administrator ingress if needed.

Publishing patches `kong.yml` in the ConfigMap and the gateway Deployment's pod-template annotation to request a rolling restart. New pods load that persisted configuration. A successful API response means **rollout requested**, not rollout completed. Verify `oc rollout status deployment/mcp-gateway` and authenticated calls before considering the revision live. This rollout strategy deliberately keeps the Kong Admin API disabled; see [Kong DB-less configuration](https://developer.konghq.com/gateway/db-less-mode/).

Replicas can temporarily use different policies during a rolling update; removal or revocation is not immediate across all pods. ConfigMap and Deployment writes are not atomic. If publication fails between them, the API reports failure and records an event; inspect cluster state and retry the same reviewed revision. Requests serialize within the single control-plane process. Do not deploy multiple writable control-plane instances against this SQLite database.

Do not manage the same ConfigMap concurrently with GitOps or repeated static `oc apply` operations: those can overwrite published registrations. Treat the registry as the owner once enabled. Export and back up the configuration and persistent database. Roll back by restoring a known-good draft and publishing it; version history/automatic rollback are not yet implemented. Registry changes do not validate backend availability or perform live IdP discovery.

## Verification

```sh
python3 -m unittest discover -s control-plane -v
```

CI also builds the control-plane container and runs its tests as an arbitrary UID with a read-only root filesystem, renders the optional deployment, and parses a generated registry configuration using Kong.
