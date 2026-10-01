# Configure Auth0 for the MCP control plane

Tenant Domain: `dev-861rc8yhbzrjbgts.us.auth0.com`, verified against its public OIDC discovery endpoint. The application client ID and private client secret must come from your Regular Web Application's Settings. This repository contains a tenant-specific template, not application credentials.

## 1. Create a dedicated control-plane API

In **Applications → APIs → Create API**, set:

- Name: `MCP Control Plane`
- Identifier: `https://mcp-control-plane`
- Signing Algorithm: `RS256`

On **Permissions**, add `control-plane:admin`. On **Settings**, enable **RBAC** and **Add Permissions in the Access Token**. The backend checks the verified token's `permissions` array, rather than accepting a requested scope as administrator authorization. Use an API audience separate from your MCP server audiences.

See [Auth0 API RBAC](https://auth0.com/docs/get-started/apis/enable-role-based-access-control-for-apis) and [permissions in access tokens](https://support.auth0.com/center/s/article/Adding-RBAC-Permissions-to-Access-Tokens).

## 2. Grant administrator permission

In **User Management → Roles**, create `MCP Control Plane Admin`, add the API's `control-plane:admin` permission, and assign the role to your administrator user. A normal user can complete Auth0 login but cannot access the registry without this permission. Role names are descriptive; the assigned permission is what the control plane enforces.

## 3. Create a Regular Web Application

In **Applications → Applications → Create Application**, choose **Regular Web Application**, named `MCP Control Plane UI`. In **Settings**, set:

| Setting | Local development value |
| --- | --- |
| Allowed Callback URLs | `http://127.0.0.1:8080/auth/callback` |
| Allowed Logout URLs | `http://127.0.0.1:8080/` |
| Application Login URI (optional) | `http://127.0.0.1:8080/auth/login` |

Use authorization code flow. The backend sends the client secret to the token endpoint using `client_secret_post` authentication. Enable the user connection you want to use for this application (for example, Auth0's database connection). No SPA app or client credentials grant is required for interactive UI login, and no Auth0 Management API permissions are needed.

For deployment, replace the loopback URLs with your actual HTTPS control-plane hostname. The callback must match `CONTROL_PLANE_PUBLIC_URL + /auth/callback` exactly; do not use wildcard callbacks. Do not mix `localhost` and `127.0.0.1`, since cookie binding depends on the same origin.

See [Auth0 authorization code login](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow/add-login-auth-code-flow).

## 4. Configure and start locally

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r control-plane/requirements.txt
cp control-plane/.env.example control-plane/.env
```

Edit the ignored `.env` file locally with the application's exact Domain, Client ID and Client Secret. Then:

```sh
set -a
. control-plane/.env
set +a
python3 control-plane/app.py
```

The app does not automatically load `.env`; the shell commands above export it. Open http://127.0.0.1:8080, choose **Sign in with Auth0**, and authenticate as the user granted administrator permission. For HTTPS deployment, the browser uses Secure cookies even when TLS terminates at ingress; the configured public URL determines this, not untrusted forwarded headers.

## 5. Verify

- Signed-out `/api/servers` returns 401.
- A user without the administrator permission gets 403 at login completion.
- An administrator can create a draft, preview configuration, and publish if Kubernetes integration is enabled.
- `/api/status` records the Auth0 subject for changes.
- Sign out invalidates the local session and redirects to Auth0 logout.

If login fails with 403, check role assignment, the API audience, and the **Add Permissions in the Access Token** toggle. For callback errors, check the exact callback URL, application type, and credentials. For discovery failures, check the full tenant Domain, outbound DNS/HTTPS, and trusted TLS certificates. Token encryption must be disabled; the verifier accepts signed RS256 JWTs, not encrypted or opaque tokens.

The tests use locally signed JWTs and mocked discovery/code exchange to verify security behavior. A live tenant login still needs your application's real settings and credentials.
