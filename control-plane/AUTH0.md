# Optional Auth0 configuration example

The application uses a generic OIDC client. This guide supplies an Auth0 configuration through the same `OIDC_*` settings available for other providers. The core code, default environment template, UI, and deployment have no Auth0-specific dependency.

## Tenant setup

1. Under **Applications → Applications**, create a **Regular Web Application** named `MCP Control Plane` for authorization code flow. Enable the intended login connection under the application's **Connections** tab.
2. In the application's **Settings**, register these exact local URLs and save:

   | Setting | Local value |
   | --- | --- |
   | Allowed Callback URLs | `http://127.0.0.1:8080/auth/callback` |
   | Allowed Logout URLs | `http://127.0.0.1:8080/` |

   `localhost` and `127.0.0.1` are different hosts. For deployment, use the actual HTTPS control-plane origin plus `/auth/callback` and `/`, respectively.
3. Copy the exact **Domain**, **Client ID**, and private **Client Secret**. Construct `OIDC_ISSUER` as `https://<Domain>/`, retaining the trailing slash. Match token endpoint authentication to `client_secret_post` for the configuration below. Keep the secret only in the ignored local file or a deployment Secret.
4. Under **Applications → APIs**, create an API named `MCP Control Plane` with identifier **`https://mcp-control-plane`** (no trailing slash) and **RS256** signing. This identifier is a logical audience and need not resolve as a website. It must exactly match both `OIDC_API_AUDIENCE` and the `audience` in `OIDC_AUTHORIZATION_PARAMS`.
5. In the API's **Permissions** tab, add `control-plane:admin`. In **Settings**, enable **RBAC** and **Add Permissions in the Access Token**, then save.
6. Configure application authorization separately from user authorization. For this API's **User-delegated Access**, select **Per-app authorization** under **Settings → Application Access Policy**. Under **Application Access**, click **Edit** beside the regular web application `MCP Control Plane`, select **Grant Access** for **User-delegated Access**, select `control-plane:admin`, and save. Its granted count should be **1 / 1** when this is the API's only permission. Browser login uses user-delegated access; granting **Client Access** alone does not enable it. Machine-to-machine access is not needed for this login setup.
7. Under **User Management → Roles**, create an administrator role, add this API's `control-plane:admin` permission, and assign it to your intended application login user under **User Management → Users**. Being an Auth0 Dashboard administrator does not grant application administrator permissions. Do not grant Auth0 Management API privileges to the control-plane application.

See [Auth0 RBAC](https://auth0.com/docs/get-started/apis/enable-role-based-access-control-for-apis) and [authorization code login](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow/add-login-auth-code-flow).

The per-application grant controls which permissions the application can request; the user's role controls which permissions that user has. Both are required by this setup. See [Auth0 API application access policies](https://auth0.com/docs/get-started/apis/api-access-policies-for-applications).

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
REGISTRY_DATABASE=control-plane/registry.sqlite3
```

The `audience` authorization parameter and `permissions` claim are provider-specific settings in this example, not OIDC-standard requirements or hardcoded defaults. The access-token authorization policy requires a signed JWT rather than an opaque token. Configure discovery to advertise `end_session_endpoint` if federated OIDC logout is needed; otherwise logout remains local. The application never constructs Auth0's proprietary `/v2/logout` URL.

Follow the [generic local setup](README.md#local-setup) to install requirements, export this ignored file, and start the service. For OpenShift, use the generic issuer/client Secret and add the settings above to the deployment environment. Secrets never belong in committed examples.

## Start and verify

Run from the repository root after activating the virtual environment and installing requirements:

```sh
set -a
. control-plane/.env
set +a
python3 control-plane/app.py
```

Keep this process running. Open `http://127.0.0.1:8080/`, select **Sign in with your identity provider**, and authenticate with the user assigned the administrator role. Successful login returns to the registry UI. Restart the process after changing environment settings and begin a fresh login; callback URLs are single-use and must not be replayed.

`CONTROL_PLANE_PUBLIC_URL` is the browser UI origin. `GATEWAY_PUBLIC_URL` is the MCP gateway's HTTPS origin, without `/mcp`; use the actual OpenShift Route hostname when running the gateway in CRC. Opening the gateway root does not open the registry UI. These URLs can differ when the control plane runs locally and the gateway runs in OpenShift. Local gateway TLS certificates must be trusted by connecting clients.

## Troubleshooting login

The callback currently returns a generic `Login was declined` when the provider returns an OAuth error. Read the callback URL's `error` and `error_description`, or check Auth0's **Monitoring → Logs**, for the cause. Do not share full callback URLs containing authorization codes or state, credentials, or tokens.

| Symptom or provider error | What to check |
| --- | --- |
| Callback URL mismatch | Save the exact `http://127.0.0.1:8080/auth/callback` in the selected application's Allowed Callback URLs. Use the matching Client ID and browser origin. |
| `Service not found: https://mcp-control-plane` | Create the API in the same tenant, or align both audience settings with its exact Identifier. A web application alone does not create this API. |
| Client is not authorized to access the resource server | Grant the regular web application User-delegated Access to this API and select `control-plane:admin`. With per-app authorization, defining the permission alone is insufficient. |
| `Administrator authorization required` | Check the login user's role, API RBAC, Add Permissions in the Access Token, and the configured `/permissions` claim policy. Begin a fresh login after changes. |
| Identity provider unavailable or login failed | Check the issuer, credentials, token endpoint authentication method, TLS/network reachability, and provider logs. |
| Connection refused at `127.0.0.1:8080` | Start the control-plane process with the environment exported and keep it running. |

This control-plane API audience is independent of MCP resource audiences. For MCP clients, the selected authorization server must support the MCP OAuth requirements, including RFC 8707 `resource` requests and resource-bound tokens. A provider working for administrator OIDC login is not evidence that its MCP OAuth client flow is interoperable. Consult [the interoperability profile](../docs/INTEROPERABILITY.md).
