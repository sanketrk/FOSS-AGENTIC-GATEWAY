# FOSS-AGENTIC-GATEWAY documentation

| Guide | Contents |
| --- | --- |
| [Project overview](../README.md) | Community and enterprise value, examples, and contribution ideas |
| [Contributing](../CONTRIBUTING.md) | Plugin development, proposals, testing, and pull requests |
| [Gateway setup](gateway/setup.md) | Configuration, build, OpenShift deployment, and smoke checks |
| [Control-plane setup](control-plane/setup.md) | Generic OIDC, registry API/UI, and publication |
| [Optional Auth0 setup](providers/auth0.md) | Application/API grants, roles, callbacks, and troubleshooting |
| [Protocol interoperability](protocols/interoperability.md) | Implemented profiles and their limits |
| [A2A plugin](protocols/a2a.md) | JSON-RPC and REST transport, Agent Cards, authentication, and configuration |
| [Token exchange](plugins/token-exchange.md) | RFC 8693 MCP/A2A upstream exchange and credential mounting |
| [Python SDK](../sdk/python/README.md) | Gateway clients and mandatory JWT exchange policy for responders |
| [Banking examples](../examples/banking/README.md) | Runnable agent-to-agent and agent-to-MCP scenarios with token exchange |
| [Project migration](migration/project-rename.md) | Repository, directory, deployment, and identity migration |

The project contains protocol-specific plugins. The administrator control plane currently manages the MCP registry; A2A routes and token exchange remain deployment-managed features.
