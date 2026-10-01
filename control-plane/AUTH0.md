# Optional Auth0 configuration example

The application uses a generic OIDC client. This guide supplies an Auth0 configuration through the same `OIDC_*` settings available for other providers. The core code, default environment template, UI, and deployment have no Auth0-specific dependency.

## Tenant setup

1. Create an API named `MCP Control Plane` with identifier `https://mcp-control-plane` and RS256 signing.
2. Add `control-plane:admin`; enable RBAC and **Add Permissions in the Access Token**.
3. Create an administrator role containing this permission and assign it only to your administrator user.
4. Create a **Regular Web Application** for authorization code flow. Set its callback to `http://127.0.0.1:8080/auth/callback` and allowed logout URL to `http://127.0.0.1:8080/`. Use your real HTTPS origin for deployment.
5. Copy the exact Domain, Client ID, and private Client Secret from the application's settings. Enable the intended user connection. Do not grant Auth0 Management API privileges to this application.

See [Auth0 RBAC](https://auth0.com/docs/get-started/apis/enable-role-based-access-control-for-apis) and [authorization code login](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow/add-login-auth-code-flow).

## Generic application settings for this provider

In the ignored `control-plane/.env`, use your actual tenant and credentials:

```sh
AUTH_MODE=oidc
OIDC_ISSUER=https://YOUR-TENANT.REGION.auth0.com/
OIDC_CLIENT_ID=YOUR-CLIENT-ID
OIDC_CLIENT_SECRET=YOUR-PRIVATE-CLIENT-SECRET
OIDC_SCOPES="openid profile email control-plane:admin"
OIDC_TOKEN_AUTH_METHOD=client_secret_post
OIDC_API_AUDIENCE=https://mcp-control-plane
OIDC_ADMIN_CLAIM_SOURCE=access_token
OIDC_ADMIN_CLAIM=/permissions
OIDC_ADMIN_VALUE=control-plane:admin
OIDC_AUTHORIZATION_PARAMS='{"audience":"https://mcp-control-plane"}'
CONTROL_PLANE_PUBLIC_URL=http://127.0.0.1:8080
GATEWAY_PUBLIC_URL=https://mcp.company.com
```

The `audience` authorization parameter and `permissions` claim are provider-specific settings in this example, not OIDC-standard requirements or hardcoded defaults. The access-token authorization policy requires a signed JWT rather than an opaque token. Configure discovery to advertise `end_session_endpoint` if federated OIDC logout is needed; otherwise logout remains local. The application never constructs Auth0's proprietary `/v2/logout` URL.

Follow the [generic local setup](README.md#local-setup) to install requirements, export this ignored file, and start the service. For OpenShift, use the generic issuer/client Secret and add the settings above to the deployment environment. Secrets never belong in committed examples.

This control-plane API audience is independent of MCP resource audiences. For MCP clients, the selected authorization server must support the MCP OAuth requirements, including RFC 8707 `resource` requests and resource-bound tokens. A provider working for administrator OIDC login is not evidence that its MCP OAuth client flow is interoperable. Consult [the interoperability profile](../docs/INTEROPERABILITY.md).
