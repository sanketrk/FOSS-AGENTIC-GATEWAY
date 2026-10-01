# Contributing to FOSS-AGENTIC-GATEWAY

Help make agent connections a reusable part of the open-source ecosystem. Contributions can be plugins, bug fixes, protocol interoperability reports, SDK improvements, deployment guides, or working examples.

## Start with a problem

For a new capability, [open an issue](https://github.com/sanketrk/FOSS-AGENTIC-GATEWAY/issues/new) describing who needs it, a concrete workflow, and the behavior you propose. Include what already exists, what you would add, and how you would demonstrate it. Small fixes and documentation improvements can go straight to a pull request.

Shared concerns are good starting points: agent-to-agent observability, access policy, audit, usage controls, discovery, routing, or application integration. Describe which layer your change affects and how it works alongside existing plugins.

For example, an observability proposal could link calls across two agents and an MCP server, identify slow or failed steps, and explain what instrumentation the agents must provide. Keep credentials and sensitive payloads out of traces. A model-routing proposal could define selection criteria and demonstrate a policy between two approved model endpoints. These examples are contribution directions; tracing and model routing are not implemented project features.

## Find your starting point

| Area | Where to work |
| --- | --- |
| Tool protocol and incoming access | [MCP plugin](gateway/plugins/mcp/) |
| Agent protocol and incoming access | [A2A plugin](gateway/plugins/a2a/) |
| Downstream credentials | [Token-exchange plugin](gateway/plugins/token-exchange/) |
| Administrator workflows | [Control plane](apps/control-plane/) |
| Application integration | [Python SDK](sdk/python/) |
| Runnable scenarios | [Banking examples](examples/banking/) |
| Automated validation | [Tests](tests/) and [CI workflow](.github/workflows/ci.yml) |

The three project plugins are Apache-2.0 source. They use Kong's plugin mechanism; see the [Kong custom-plugin documentation](https://developer.konghq.com/custom-plugins/) for the development API.

## Adding a plugin

1. Create `gateway/plugins/<name>/handler.lua` and `schema.lua` with a focused responsibility and validated configuration.
2. Install it in the [gateway Dockerfile](gateway/Dockerfile) and enable its name in the image and any deployment configuration that needs it. Enabling a plugin makes it available; attaching it to a route activates it.
3. Choose its execution priority deliberately and test it with the other plugins used on that route. If it obtains backend credentials, preserve authentication before exchange and correct audience binding. The [shared authentication contract](docs/plugins/token-exchange.md#shared-authentication-contract) explains the current integration.
4. Add a runnable configuration or example, appropriate tests, and documentation describing supported behavior and limits.

A router that changes the destination must keep destination selection, audience, scopes, and credentials consistent. New route types need explicit access policy and credential handling. An observability plugin needs a clear propagation and collection design; tracing a complete workflow also requires cooperating agents and services.

Keep protocol handling and business logic separate. Prefer open standards and configurable providers. Preserve TLS verification, secret isolation, and backend authorization. Document any provider-specific integration explicitly.

## Verify your contribution

Run checks appropriate to what changed. The [CI workflow](.github/workflows/ci.yml) is the full reference: it tests the control plane and SDK, builds Kong, checks plugin behavior and configuration, and exercises the banking topology with real token exchange.

For Python work, create an environment and install the package dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r apps/control-plane/requirements.txt -e sdk/python
```

Then run the relevant suite:

```sh
PYTHONPATH=apps/control-plane python -m unittest discover -s tests/control-plane -v
python -m unittest discover -s sdk/python/tests -v
PYTHONPATH=examples/banking/infrastructure:examples/banking/transaction-review-agent:examples/banking/accounts-mcp-server:examples/banking/transactions-mcp-server \
  python -m unittest discover -s tests/banking -v
```

The banking discovery suite skips tests that require a running Compose topology. Follow the [banking setup](examples/banking/README.md) and CI workflow for full gateway integration. Lua plugin checks run inside the gateway image; the CI workflow provides the exact commands.

For documentation changes, check links, commands, and any Mermaid diagram syntax. Keep the README focused on purpose and value, and put implementation details in the linked guides.

## Send a pull request

Explain the problem, the resulting behavior, and how you verified it. Link the proposal or bug report when relevant. Include any new dependencies and their licenses. A new feature should come with an example a reviewer can run.

Contributions are made under this repository's [Apache-2.0 license](LICENSE). Keep credentials, real customer data, and private deployment configuration out of examples and tests.
