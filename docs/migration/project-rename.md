# Migration to FOSS-AGENTIC-GATEWAY

The project expands beyond MCP to include A2A routing and OAuth token exchange. Its repository is now [sanketrk/FOSS-AGENTIC-GATEWAY](https://github.com/sanketrk/FOSS-AGENTIC-GATEWAY). Existing Git history is retained. The protocol profiles are unchanged by this rename.

## Source layout

| Previous path | Current path |
| --- | --- |
| `control-plane/` | `apps/control-plane/` |
| `control-plane/test_*.py` | `tests/control-plane/test_*.py` |
| `control-plane/README.md` | `docs/control-plane/setup.md` |
| `control-plane/AUTH0.md` | `docs/providers/auth0.md` |
| Root `Dockerfile` | `gateway/Dockerfile` |
| `kong/plugins/` | `gateway/plugins/` |
| `kong/kong.yml`, `kong/nginx-extra.conf` | `gateway/config/` |
| `deploy/openshift/` base | `deploy/openshift/gateway/` |
| `deploy/control-plane/` overlay | `deploy/openshift/control-plane/` |
| `examples/a2a-kong.yml` | `examples/gateway/a2a.yml` |
| Flat protocol/plugin guides | `docs/protocols/`, `docs/plugins/` |

Build the gateway from the repository root with `docker build -f gateway/Dockerfile -t foss-agentic-gateway:latest .`. Build the administrator service with `docker build -t foss-agentic-control-plane:latest apps/control-plane`. Binary OpenShift builds must set `spec.strategy.dockerStrategy.dockerfilePath` to `gateway/Dockerfile`.

Move ignored `.env` and SQLite files with the control-plane directory and update any relative `REGISTRY_DATABASE` path. Do not overwrite local credentials with the example. Run local checks with `PYTHONPATH=apps/control-plane python3 -m unittest discover -s tests/control-plane -v`.

## Deployment and identity

New manifests use `foss-agentic-gateway`, `foss-agentic-gateway-kong`, and `foss-agentic-control-plane` resource prefixes. Applying them to an existing namespace creates separate resources; Kubernetes does not rename old Deployments, Services, Secrets, or PVCs. Migrate images, ConfigMap ownership, service-account permissions, data, and ingress deliberately before retiring any old deployment. Back up the registry first. Existing local cluster resources and route hostnames are not automatically renamed by this source migration.

Publisher defaults target the new gateway Deployment and ConfigMap. To operate an existing deployment temporarily, set `PUBLISH_CONFIGMAP=mcp-gateway-kong` and `PUBLISH_DEPLOYMENT=mcp-gateway`, with matching least-privilege Role resource names. The gateway URL still must describe the actual deployed HTTPS route.

New generic administrator-role examples use `agentic-admin`; deployments with an explicit `OIDC_ADMIN_VALUE` can retain their current role. Session cookies now use `agentic-session`/`agentic-login` (with `__Host-` prefixes on HTTPS); restart the service and begin a fresh login.

New Auth0 examples use the audience `https://foss-agentic-control-plane`. Existing tenants can keep `https://mcp-control-plane` in both `OIDC_API_AUDIENCE` and the optional authorization `audience` parameter; an application display-name change does not require an API identifier change. If changing an identifier, configure the API, user-delegated grant, permission, and role first, then update both audience settings and sign in again. Callback/logout URLs remain tied to `CONTROL_PLANE_PUBLIC_URL`, not the product name.

Plugin IDs `mcp-gateway`, `a2a-gateway`, and `mcp-token-exchange` retain their protocol-specific names. Lua modules, public `/mcp` paths, scope policies, Agent Card formats, and configured resource audiences are not branding identifiers. The control plane remains an MCP registry; it does not yet administer A2A or exchange policies.
