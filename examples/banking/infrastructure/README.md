# Local banking infrastructure

This folder holds the disposable OAuth issuer/STS, certificate and credential preparation, fixed routing policies, and a small HTTP adapter. Application business code lives in the four sibling component folders. Token verification lives in the shared Python SDK.

The infrastructure image runs `python -m banking_demo.prepare` or `python -m banking_demo.issuer`. It is a synthetic local fixture, not a production IdP. See [setup](../README.md).
