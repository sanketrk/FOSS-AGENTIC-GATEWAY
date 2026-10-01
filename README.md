# FOSS-AGENTIC-GATEWAY

**One place to connect AI agents to your enterprise's tools and other agents.**

As an enterprise adds AI agents, each one needs access to different systems. Connecting them individually means more credentials to manage, more access rules to maintain, and more places to make changes.

FOSS-AGENTIC-GATEWAY gives those connections a shared entry point that you host in your own infrastructure. Agents use the gateway to reach approved tools and other agents. Your enterprise decides which connections are available and what access each requires.

```text
Your AI agents → Enterprise gateway → Tools and other agents
```

## What this gives your enterprise

- **Simpler connections.** Agents use gateway addresses, while internal service locations stay in gateway configuration.
- **Control over access.** Configure who can reach each service and which permissions they need.
- **Only the permission needed for each call.** Token exchange can narrow a caller's broad access to the specific permission a target agent or tool needs.
- **Less credential management for agents.** With token exchange enabled, agents use their own credentials and the gateway obtains the permission needed for each internal service.
- **Independence from a single vendor.** Connect services using open standards and configure your identity provider.
- **A foundation you can own.** Deploy and extend the open-source project inside your infrastructure.

## Why token exchange matters

A user may have 100 entitlements across the enterprise. When an agent acts for that user, the target service should receive only the permission needed for that call. This narrowing of access is called **downscoping**.

For example, a transaction-review agent may need just `review:execute`. The gateway asks your identity service for a token limited to that agent and that permission. Access to payments, customer administration, and other services stays out of the token sent to the target.

The gateway checks the incoming permission and requests the configured scope for the target. Your identity service must check that the caller is entitled to receive it before issuing the narrower token. The backend then checks the token before doing any work.

This limits what the target can do with the credential it receives. Agents also avoid holding a separate credential for every backend. With the appropriate identity policy, the backend can identify both the original caller and the gateway acting for it.

Token exchange is available as a shared plugin for both tool calls and agent-to-agent calls. Enable it on the routes that need it; the [banking examples](examples/banking/README.md) require it. See the [setup guide](docs/plugins/token-exchange.md) for configuration and identity requirements.

## A banking example

A banking assistant needs to answer a question about an account and a recent purchase. Through the gateway, it can:

1. Ask the accounts service for an account summary.
2. Ask the transactions service for recent transactions.
3. Ask a transaction-review agent to summarize a purchase.

Each service receives only its configured permission: account summaries, recent transactions, or transaction review. The examples use synthetic data and demonstrate these connections with four separate components: two agents and two tool servers.

[Run the banking examples →](examples/banking/README.md)

## What you can connect

| Connection | Supported through the gateway |
| --- | --- |
| Agents calling tools | MCP servers |
| Agents calling other agents | A2A over JSON-RPC or REST |
| Gateway obtaining backend access | OAuth token exchange |
| Administrators managing connections | Control-plane UI and registration API for MCP servers |

MCP and A2A are open protocols for tool access and agent communication. The [Python SDK](sdk/python/README.md) helps applications call the gateway and check backend permissions.

The control plane uses OIDC for administrator login. Auth0 is one documented setup option. A2A connections and token exchange currently use deployment configuration rather than the control-plane UI.

## Get started

```sh
git clone https://github.com/sanketrk/FOSS-AGENTIC-GATEWAY.git
cd FOSS-AGENTIC-GATEWAY
```

Start with the [banking examples](examples/banking/README.md) to see the complete flow. For your own environment, follow the [gateway setup guide](docs/gateway/setup.md) and [control-plane setup guide](docs/control-plane/setup.md).

You supply the internal services, identity configuration, and deployment environment. Each service remains responsible for the work it performs and for checking access. Production deployments also need network restrictions, monitoring, and operational policies. The [interoperability guide](docs/protocols/interoperability.md) describes supported behavior and current limits.

## Explore the project

| Location | Purpose |
| --- | --- |
| `gateway/` | Gateway and its MCP, A2A, and token-exchange plugins |
| `apps/control-plane/` | Administrator UI and registration API |
| `sdk/python/` | Client and backend verification SDK |
| `examples/` | Gateway configurations and runnable banking examples |
| `deploy/openshift/` | OpenShift and Kubernetes deployment manifests |
| `docs/` | Setup and technical guides |
| `tests/` | Automated checks |

See the [documentation index](docs/README.md) for all guides.

## Open source

Licensed under [Apache-2.0](LICENSE). Built on Kong Gateway OSS and OpenResty. The project contains no Kong Enterprise modules; dependency license information is available in the [gateway setup guide](docs/gateway/setup.md).
