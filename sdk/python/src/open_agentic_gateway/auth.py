"""Verify an issuer's signed claims for the gateway-to-backend exchange policy."""
import math

import jwt


class AuthenticationError(Exception):
    """The incoming token does not satisfy the backend's exchange policy."""


class ExchangeTokenVerifier:
    """Require a resource-bound JWT naming the authorized gateway actor.

    The STS must issue an RFC 8693 act.sub claim for this policy. A token cannot
    prove which OAuth grant created it; enforcement trusts the issuer's signed
    audience, actor and scope claims, never a response receipt or identity header.
    """
    def __init__(self, *, issuer, audience, scopes, gateway_actor, jwks, subjects=None,
                 algorithms=("RS256",)):
        if not issuer.startswith("https://") or not audience or not gateway_actor or not scopes:
            raise ValueError("HTTPS issuer, backend audience, scopes and gateway actor are required")
        allowed = {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
        if not algorithms or set(algorithms) - allowed:
            raise ValueError("Only configured asymmetric signing algorithms are supported")
        self.issuer, self.audience, self.scopes = issuer, audience, tuple(scopes)
        self.gateway_actor, self.subjects = gateway_actor, None if subjects is None else frozenset(subjects)
        self.algorithms = tuple(algorithms)
        self.keys = {}
        for value in jwks.get("keys", []):
            kid = value.get("kid")
            if not isinstance(kid, str) or not kid or kid in self.keys:
                raise ValueError("JWKS must contain distinct nonempty key IDs")
            if "d" in value or value.get("use", "sig") != "sig" or value.get("kty") not in ("RSA", "EC", "OKP"):
                raise ValueError("JWKS must contain asymmetric signing keys")
            self.keys[kid] = jwt.PyJWK.from_dict(value).key
        if not self.keys: raise ValueError("At least one public verification key is required")

    def verify(self, authorization):
        if not isinstance(authorization, str): raise AuthenticationError("Exchanged Bearer token required")
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 32768:
            raise AuthenticationError("Exchanged Bearer token required")
        token = parts[1]
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.algorithms or header.get("kid") not in self.keys:
                raise AuthenticationError("Invalid exchanged token")
            claims = jwt.decode(token, self.keys[header["kid"]], algorithms=list(self.algorithms),
                                issuer=self.issuer, audience=self.audience,
                                options={"require": ["iss", "aud", "sub", "exp", "iat"]})
            for name in ("exp", "iat"):
                value = claims[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise AuthenticationError("Invalid token timestamps")
            actor = claims.get("act")
            if not isinstance(actor, dict) or actor.get("sub") != self.gateway_actor:
                raise AuthenticationError("Authorized gateway actor required")
            scope = claims.get("scope")
            if not isinstance(scope, str) or not set(self.scopes).issubset(scope.split()):
                raise AuthenticationError("Backend scopes required")
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise AuthenticationError("Token subject required")
            if self.subjects is not None and claims["sub"] not in self.subjects:
                raise AuthenticationError("Subject is not authorized")
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AuthenticationError("Invalid or expired exchanged token") from None
