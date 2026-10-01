# Gateway setup and operations

Build, configuration, deployment, and validation details for Open Agentic Gateway. Commands run from the repository root. Start with the [project overview](../../README.md) for the purpose and scope.

## Features

- Kong Gateway OSS 3.9 by default. Supports MCP Streamable HTTP revision `2026-07-28` and configurable legacy compatibility for `2025-11-25` and `2025-03-26`.
- Current `2026-07-28` requests use POST-only stateless semantics. Legacy requests retain POST, optional GET SSE, DELETE, session identifiers, and `Last-Event-ID` pass-through.
- Enforces current JSON request content type, JSON+SSE `Accept`, `Mcp-Method`, protocol-version agreement with `_meta`, and `Mcp-Name` where required. Legacy requests use version negotiation and JSON-RPC envelope validation without imposing current-only headers.
- Session IDs, event IDs, and response session headers are preserved only in legacy mode. The gateway does not create or manage sessions; the upstream remains responsible for session lifecycle and stream resumption.
- Publishes OAuth 2.0 Protected Resource Metadata at root and MCP-path well-known endpoints, and returns an OAuth `WWW-Authenticate` challenge that points clients to metadata and configured required scopes.
- Advertises multiple configured authorization servers and validates JWTs only using the discovery URL explicitly paired with the token's configured issuer.
- Fail-closed bearer JWT verification using OIDC discovery/JWKS, exact issuer and audience checks, algorithm allow-list, optional scopes, and TLS verification.
- Strips the bearer token before proxying by default; forwarding it to the upstream is an explicit opt-in.
- SSE-friendly upstream timeouts, buffering disabled, and retries disabled to avoid replaying non-idempotent MCP requests.
- No Admin API listener; declarative configuration; non-root/read-only-rootfs OpenShift deployment.

## Central enterprise gateway

Agents connect to one public gateway host and select a registered server endpoint. Each server executes its own MCP methods and owns its session lifecycle. Kong routes requests and enforces each endpoint's OAuth policy.

| Agent endpoint | Sample internal server | Token audience | Required scope |
| --- | --- | --- | --- |
| `/mcp` | `mcp-server:3000` (path preserved) | `https://mcp.example.com/mcp` | `mcp:access` |
| `/mcp/server-a` | `mcp-server-a:3000/mcp` | `https://mcp.example.com/mcp/server-a` | `mcp:server-a:access` |
| `/mcp/server-b` | `mcp-server-b:3000/mcp` | `https://mcp.example.com/mcp/server-b` | `mcp:server-b:access` |

These are generic placeholders. The `services` list in `gateway/config/kong.yml` is the declarative server registry. Add a service and route for each actual MCP server, with its upstream URL and independent plugin configuration. Update the embedded configuration in `deploy/openshift/gateway/kong-config.yaml` to match; validation checks both copies. For file-managed deployments, registry changes require a controlled configuration rollout. The optional [control plane](../../docs/control-plane/setup.md) adds an authenticated registration API, browser UI, SQLite persistence, and explicit publish workflow.

Configure agents with the public endpoint(s) they may access, never the backend URL. The authorization server must issue access tokens bound to the endpoint's public resource URI and scopes. MCP clients send the standard RFC 8707 `resource` parameter in authorization and token requests. A token for server A is rejected at server B unless it explicitly satisfies server B's policy. Policies govern access to a whole server; tool-level authorization remains the upstream's responsibility.

Each endpoint has its own `/.well-known/oauth-protected-resource/mcp/<server>` metadata URL. The root metadata endpoint describes only the default `/mcp` resource, not a catalog of all servers. The gateway does not aggregate tools or dynamically discover backends.

Routes use exact regex paths so unregistered names, suffixes, and trailing slashes return 404 instead of reaching the default server. Named routes strip their public path and use the service URL's `/mcp` backend path. Set that URL path to the actual backend endpoint. See [Kong route path handling](https://developer.konghq.com/gateway/entities/route/).

At deployment, restrict MCP backend ingress to the gateway using your network controls. Hosting a gateway alone does not prevent agents from connecting directly to accessible backends. This baseline does not yet include centralized audit records, metrics export, rate-limit policies, upstream health checks, or validated backend failover; configure and test these before treating it as production enterprise infrastructure.

To smoke-test a named server with a correctly scoped token:

```sh
GATEWAY_URL=https://mcp.company.com \
MCP_PATH=/mcp/server-a \
ACCESS_TOKEN="$ACCESS_TOKEN" \
sh tests/smoke.sh
```

## Registration API and control plane UI

The optional [lightweight control plane](../../docs/control-plane/setup.md) lets administrators register, edit, and remove MCP servers, review a generated gateway configuration, and publish it through an OpenShift rollout. Registration changes remain drafts until published. The control plane uses OIDC login with a configurable trusted administrator claim; agents still authenticate with their separate OAuth policies at the gateway. Kong's Admin API remains disabled.

Run it locally with Python or deploy the optional `deploy/openshift/control-plane` overlay. See its [setup, API, and publication documentation](../../docs/control-plane/setup.md) for instructions and rollout limitations.

## Configure

Before building or deploying, edit the declarative config in [gateway/config/kong.yml](../../gateway/config/kong.yml) or the OpenShift ConfigMap in [deploy/openshift/gateway/kong-config.yaml](../../deploy/openshift/gateway/kong-config.yaml):

1. Set each upstream MCP server URL and its route-specific audience and scopes.
2. Set `resource_url` and `resource_metadata_url` to the public HTTPS MCP URL and its path-specific Protected Resource Metadata URL. Set `metadata_paths` to both the path-specific and root well-known paths reachable through your ingress.
3. Add one or more `authorization_servers`, each pairing an issuer URL with its exact HTTPS OIDC discovery URL. Their `issuer` values are advertised to clients and are the only issuers accepted for JWT verification. The sample IdP and gateway URLs are placeholders.
4. Set the expected access-token audience. Keep TLS verification enabled. Configure `required_scopes` for enforced permissions and `scopes_supported` to advertise the scopes clients can request.
5. If requests carrying browser `Origin` headers are expected, add exact trusted origins to `allowed_origins`. Origins are rejected by default. This check is not CORS support; browser clients also need an explicitly configured CORS policy at the edge.
6. Ensure the MCP upstream validates `Origin` itself as recommended by the MCP specification, especially if the service is reachable from browsers.
7. Set `forward_bearer_token: true` only when the upstream needs the original access token for its own authorization checks; this shares that credential with the backend service.

MCP clients discover the configured authorization servers through the gateway's public resource metadata and then run their own OAuth authorization flow against the selected IdP to obtain a token. The IdP—not this gateway—handles user login, consent, client registration, and code/token issuance. The gateway does not redirect users or mint tokens. It verifies JWT access tokens (not ID tokens or opaque tokens) against the explicitly paired OIDC discovery/JWKS endpoints, exact issuer, audience, signing algorithm, and required scopes. `signing_algorithms` defaults to `RS256`.

## Build and run

```sh
docker build --build-arg KONG_VERSION=3.9.0 -t open-agentic-gateway:latest -f gateway/Dockerfile .
```

The image installs `lua-resty-openidc` into the Kong/OpenResty Lua runtime. [gateway/config/nginx-extra.conf](../../gateway/config/nginx-extra.conf) defines its `discovery`, `jwks`, and `jwt_verification` shared dictionaries, included by Kong's HTTP configuration. The OpenShift ConfigMap mounts both the declarative Kong config and the matching Nginx include. The Nginx client body limit is 10 MiB; raise it deliberately if the upstream accepts larger MCP requests.

For a local container, first edit `gateway/config/kong.yml` with reachable upstream and OIDC values, then run:

```sh
docker run --rm -p 8000:8000 -p 8100:8100 open-agentic-gateway:latest
```

The MCP endpoint is `http://localhost:8000/mcp`. The status listener is on port 8100 and is not exposed by the OpenShift Service. Provide a valid bearer JWT in the `Authorization` header. Configure a locally reachable upstream and OIDC issuer first.

Legacy session-aware upstreams must keep session state reachable for the lifetime of a client session. Configure the upstream or its load balancer for appropriate session affinity/shared state; this gateway forwards the session identifier but does not pin traffic to an upstream instance.

## Deploy to OpenShift

Build and push the image to a registry accessible by the cluster, update `image` in [deploy/openshift/gateway/deployment.yaml](../../deploy/openshift/gateway/deployment.yaml), and update the ConfigMap as above. Then apply:

```sh
oc apply -k deploy/openshift/gateway
```

Expose the ClusterIP Service through your platform's TLS-terminating ingress or an OpenShift Route configured for HTTPS. Configure external network egress for the OIDC issuer and internal network access to the MCP upstream. Set the ingress/Route idle timeout to accommodate the SSE duration needed by your clients. The Deployment runs with a read-only root filesystem, drops all Linux capabilities, disables privilege escalation, uses an ephemeral writable Kong prefix, and does not mount a service-account token. OpenShift assigns the runtime UID; no fixed UID or privileged port is required.

The sample has no credentials, no public Route, and no permissive CORS policy. Configure TLS at the edge and apply environment-specific NetworkPolicies and request/connection limits before exposing it to untrusted networks. Protect the upstream independently; the gateway strips the raw JWT by default and does not synthesize identity headers.

### Test locally with OpenShift Local (CRC)

CRC runs a single-node OpenShift cluster for development/testing, not production. After installing CRC and starting its `openshift` preset, open a shell at this repository root and configure the bundled `oc` CLI:

```sh
eval "$(crc oc-env)"
oc login -u developer https://api.crc.testing:6443
oc project open-agentic-gateway || oc new-project open-agentic-gateway
```

The CRC `developer` account's sample password is `developer`; `crc console --credentials` displays the local cluster credentials if needed. Create a binary Docker build configuration once, then build this checkout into the cluster's internal image registry:

```sh
if ! oc get buildconfig open-agentic-gateway >/dev/null 2>&1; then
  oc new-build --binary --strategy=docker --name=open-agentic-gateway --to=open-agentic-gateway:latest
fi
oc patch buildconfig open-agentic-gateway --type=merge \
  -p '{"spec":{"strategy":{"dockerStrategy":{"dockerfilePath":"gateway/Dockerfile"}}}}'
git archive --format=tar -o /tmp/open-agentic-gateway-build.tar HEAD
oc start-build open-agentic-gateway --from-archive=/tmp/open-agentic-gateway-build.tar --follow
```

Deploy the manifests, select the image built above, and use one replica to fit CRC's single-node memory budget:

```sh
oc apply -k deploy/openshift/gateway
oc set image deployment/open-agentic-gateway \
  gateway=image-registry.openshift-image-registry.svc:5000/open-agentic-gateway/open-agentic-gateway:latest
oc scale deployment/open-agentic-gateway --replicas=1
oc rollout status deployment/open-agentic-gateway --timeout=5m
```

Forward the service from a second terminal, then check the public metadata endpoint:

```sh
oc port-forward service/open-agentic-gateway 18000:8000
```

```sh
curl -i http://127.0.0.1:18000/.well-known/oauth-protected-resource/mcp
```

With the sample configuration, metadata should return `200`, a valid-shaped current-version POST without a token should return `401` with a Protected Resource Metadata challenge, and a current-version GET should return `405`. Legacy `2025-11-25` GET/DELETE pass transport checks and reach OAuth validation (`401` without a token). These checks validate gateway startup, routing, and policy behavior; the sample issuer and `mcp-server.default.svc.cluster.local` upstream are placeholders, so they do **not** verify a successful authenticated MCP call. Configure a reachable OIDC issuer and MCP server before running the full live smoke test.

The current local deployment has been exercised on CRC/OpenShift 4.22.14: the arbitrary-UID pod reached Ready with a read-only root filesystem, plugin unit tests passed in the Kong image, metadata returned `200`, and unauthenticated requests produced the expected transport/OAuth responses. CRC deployment is a local functional check, not a substitute for testing ingress TLS/timeouts, external identity-provider integration, multi-replica session routing, or production load.

## Smoke test

The live smoke test checks metadata discovery, OAuth challenge headers, legacy transport routing, current-revision request headers and mismatch errors, and an authenticated `server/discover` request. Configure an upstream implementing MCP revision `2026-07-28`, a reachable OIDC issuer, and matching gateway resource URLs. Set `RESOURCE_URL` and `RESOURCE_METADATA_URL` if using URLs other than the documented sample values:

```sh
GATEWAY_URL=https://mcp.example.com \
RESOURCE_URL=https://mcp.example.com/mcp \
RESOURCE_METADATA_URL=https://mcp.example.com/.well-known/oauth-protected-resource/mcp \
ACCESS_TOKEN="$ACCESS_TOKEN" \
sh tests/smoke.sh
```

The repository includes a Lua plugin test suite, config/hardening validation, and a CI workflow that builds the image, parses declarative config, runs plugin tests, launches Kong as an arbitrary UID with a read-only root filesystem, checks its status endpoint, and renders the OpenShift Kustomize resources.


## License and dependencies


Files in this repository are licensed under Apache-2.0; see [LICENSE](../../LICENSE). The image is based on Kong Gateway OSS and adds `lua-resty-openidc`; consult the corresponding upstream projects for their license and notice obligations when redistributing the combined image. This repository does not include Kong Enterprise modules or other closed-source runtime components.
