"""Fixed, synthetic banking policies shared by the example fixtures and caller."""
import json
import time
import uuid
from pathlib import Path

import jwt

ISSUER = "https://issuer:8443"
GATEWAY = "https://gateway:8443"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
AGENT_ID = "banking-orchestrator"
EXCHANGE_ID = "banking-gateway"
POLICIES = {
    "review": {"path": "/a2a/transaction-review", "resource": "urn:bank:backend:transaction-review",
               "gateway_scope": "a2a:review", "backend_scope": "review:execute"},
    "accounts": {"path": "/mcp/accounts", "resource": "urn:bank:backend:accounts",
                 "gateway_scope": "accounts:read", "backend_scope": "accounts:summary"},
    "transactions": {"path": "/mcp/transactions", "resource": "urn:bank:backend:transactions",
                     "gateway_scope": "transactions:read", "backend_scope": "transactions:recent"},
}
for policy in POLICIES.values():
    policy["audience"] = GATEWAY + policy["path"]

ACCOUNT = {"account_id": "DEMO-001", "currency": "INR", "available_balance": "125000.00",
           "status": "active", "synthetic": True}
TRANSACTIONS = [
    {"id": "DEMO-TX-001", "description": "Synthetic salary credit", "amount": "75000.00", "direction": "credit"},
    {"id": "DEMO-TX-002", "description": "Synthetic utility bill", "amount": "2400.00", "direction": "debit"},
    {"id": "DEMO-TX-003", "description": "Synthetic merchant purchase", "amount": "1800.00", "direction": "debit"},
]


def issue(key, audience, scope, subject=AGENT_ID, token_use="gateway", expires=None):
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": subject, "aud": audience, "scope": scope,
              "iat": now, "exp": min(now + 300, expires) if expires else now + 300,
              "jti": str(uuid.uuid4()), "token_use": token_use}
    if token_use == "upstream": claims["act"] = {"sub": EXCHANGE_ID}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "banking-demo"})


def public_key(directory):
    jwks = json.loads((Path(directory) / "jwks.json").read_text())
    return jwt.PyJWK.from_dict(jwks["keys"][0]).key


def verify(token, key, audience, scope, token_use):
    claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER, audience=audience,
                        options={"require": ["iss", "sub", "aud", "exp", "iat"]})
    if claims.get("token_use") != token_use or scope not in claims.get("scope", "").split():
        raise ValueError("Token policy not satisfied")
    return claims


def receipt(claims):
    """Show the verified trust boundary without exposing any bearer token."""
    return {"subject": claims["sub"], "audience": claims["aud"], "scope": claims["scope"],
            "actor": claims.get("act", {}).get("sub"), "token_use": claims["token_use"]}
