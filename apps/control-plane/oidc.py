"""OIDC authorization code login with PKCE and server-side administrator sessions."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie

import jwt


class AuthError(Exception):
    def __init__(self, message="Authentication failed", status=401):
        super().__init__(message)
        self.status = status


def public_origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or any(c.isspace() for c in value)):
        raise ValueError("CONTROL_PLANE_PUBLIC_URL must be an origin without a path")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("HTTP is allowed only on loopback for local development")
    return value.rstrip("/")


def https_url(value, allow_query=False):
    if not isinstance(value, str): raise ValueError("Expected an HTTPS URL")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or (parsed.query and not allow_query) or any(c.isspace() for c in value)):
        raise ValueError("Expected an HTTPS URL without credentials, query or fragment")
    parsed.port
    return value


def claim_at_pointer(claims, pointer):
    """RFC 6901 pointer supports nested and namespaced authorization claims."""
    current = claims
    for segment in pointer[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key and all(c in "0123456789" for c in key) and (key == "0" or not key.startswith("0")):
            index = int(key)
            if index >= len(current): return None
            current = current[index]
        else:
            return None
    return current


class OIDCClient:
    def __init__(self, issuer, client_id, client_secret, audience, origin,
                 admin_claim="/roles", admin_value="agentic-admin", admin_claim_source="id_token",
                 scopes=("openid", "profile", "email"), token_auth_method="client_secret_basic",
                 authorization_params=None, discovery_url=None, resource=None,
                 signing_algorithms=("RS256",), session_seconds=900):
        self.issuer = https_url(issuer)
        self.discovery_url = https_url(discovery_url or issuer.rstrip("/") + "/.well-known/openid-configuration")
        self.origin = public_origin(origin)
        if not client_id or not admin_value: raise ValueError("OIDC client ID and admin value are required")
        if token_auth_method not in ("client_secret_basic", "client_secret_post", "none"):
            raise ValueError("Unsupported OIDC token authentication method")
        if token_auth_method != "none" and not client_secret: raise ValueError("OIDC client secret is required")
        if admin_claim_source not in ("id_token", "access_token"):
            raise ValueError("OIDC_ADMIN_CLAIM_SOURCE must be id_token or access_token")
        if admin_claim_source == "access_token" and not audience:
            raise ValueError("OIDC_API_AUDIENCE is required for access-token authorization")
        if not isinstance(admin_claim, str) or not admin_claim.startswith("/") or any(
                "~" in part.replace("~0", "").replace("~1", "") for part in admin_claim.split("/")):
            raise ValueError("OIDC_ADMIN_CLAIM must be an RFC 6901 JSON pointer")
        if not scopes or "openid" not in scopes or any(not scope or any(c.isspace() for c in scope) for scope in scopes):
            raise ValueError("OIDC_SCOPES must include openid and valid scope names")
        allowed = {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
        if not signing_algorithms or set(signing_algorithms) - allowed:
            raise ValueError("OIDC_SIGNING_ALGORITHMS must contain supported asymmetric algorithms")
        self.client_id, self.client_secret, self.audience = client_id, client_secret, audience
        self.admin_claim, self.admin_value, self.admin_claim_source = admin_claim, admin_value, admin_claim_source
        self.scopes, self.token_auth_method = tuple(scopes), token_auth_method
        self.signing_algorithms = tuple(signing_algorithms)
        self.resource = https_url(resource) if resource else None
        self.authorization_params = authorization_params or {}
        reserved = {"client_id", "client_secret", "redirect_uri", "response_type", "response_mode", "scope",
                    "state", "nonce", "code_challenge", "code_challenge_method", "resource"}
        if (not isinstance(self.authorization_params, dict) or set(self.authorization_params) & reserved
                or any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.authorization_params.items())):
            raise ValueError("OIDC_AUTHORIZATION_PARAMS must be a string map without reserved OIDC fields")
        self.session_seconds = max(60, min(int(session_seconds), 3600))
        self.secure = self.origin.startswith("https://")
        self.session_cookie = "__Host-agentic-session" if self.secure else "agentic-session"
        self.login_cookie = "__Host-agentic-login" if self.secure else "agentic-login"
        self.lock = threading.RLock()
        self.pending, self.sessions = {}, {}
        self.metadata = None
        self.keys = None

    def cookie(self, name, value, age):
        cookie = SimpleCookie()
        cookie[name] = value
        item = cookie[name]
        item["path"], item["httponly"], item["samesite"], item["max-age"] = "/", True, "Lax", str(age)
        if self.secure: item["secure"] = True
        return item.OutputString()

    @staticmethod
    def read_cookie(headers, name):
        try:
            cookies = SimpleCookie(headers.get("Cookie", ""))
            return cookies[name].value if name in cookies else ""
        except Exception:
            return ""

    def prune(self):
        now = time.time()
        for bucket in (self.pending, self.sessions):
            for key in list(bucket):
                if bucket[key]["expires"] <= now: del bucket[key]

    def discover(self):
        with self.lock:
            if self.metadata is not None: return self.metadata
            with urllib.request.urlopen(self.discovery_url, timeout=10) as response:
                metadata = json.load(response)
            if metadata.get("issuer") != self.issuer:
                raise AuthError("OIDC discovery issuer mismatch", 502)
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                try: https_url(metadata.get(field), allow_query=True)
                except ValueError: raise AuthError("Invalid OIDC discovery endpoint", 502) from None
            if metadata.get("end_session_endpoint"):
                try: https_url(metadata["end_session_endpoint"], allow_query=True)
                except ValueError: raise AuthError("Invalid OIDC logout endpoint", 502) from None
            if "code" not in metadata.get("response_types_supported", []):
                raise AuthError("Provider does not advertise authorization code flow", 502)
            if self.token_auth_method not in metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
                raise AuthError("Provider does not support the configured client authentication method", 502)
            if not set(self.signing_algorithms).intersection(metadata.get("id_token_signing_alg_values_supported", [])):
                raise AuthError("Provider does not advertise a configured ID token signing algorithm", 502)
            self.keys = jwt.PyJWKClient(metadata["jwks_uri"], timeout=10, lifespan=300)
            self.metadata = metadata
            return metadata

    def login(self):
        metadata = self.discover()
        state, binding, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
        with self.lock:
            self.prune()
            if len(self.pending) >= 1000: raise AuthError("Too many pending login requests", 429)
            self.pending[state] = {"binding": binding, "nonce": nonce, "verifier": verifier, "expires": time.time() + 300}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {
            "client_id": self.client_id, "response_type": "code", "response_mode": "query",
            "redirect_uri": self.origin + "/auth/callback", "scope": " ".join(self.scopes),
            "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        if self.resource: params["resource"] = self.resource
        params.update(self.authorization_params)
        query = urllib.parse.urlencode(params)
        return metadata["authorization_endpoint"] + ("&" if urllib.parse.urlsplit(metadata["authorization_endpoint"]).query else "?") + query, self.cookie(self.login_cookie, binding, 300)

    def decode(self, token, audience):
        if not isinstance(token, str) or len(token) > 32768:
            raise AuthError()
        self.discover()
        try:
            if jwt.get_unverified_header(token).get("alg") not in self.signing_algorithms: raise AuthError()
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=list(self.signing_algorithms), audience=audience, issuer=self.issuer,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]: raise AuthError()
            return claims
        except jwt.PyJWTError:
            raise AuthError("Invalid or expired token") from None

    def require_admin(self, claims):
        value = claim_at_pointer(claims, self.admin_claim)
        if not (isinstance(value, str) and value == self.admin_value or
                isinstance(value, list) and self.admin_value in value):
            raise AuthError("Administrator authorization required", 403)

    def verify_access(self, token):
        if not self.audience: raise AuthError("Bearer API authentication is not configured")
        claims = self.decode(token, self.audience)
        self.require_admin(claims)
        return claims

    def exchange(self, code, verifier):
        metadata = self.discover()
        params = {"grant_type": "authorization_code", "code": code,
                  "redirect_uri": self.origin + "/auth/callback", "client_id": self.client_id,
                  "code_verifier": verifier}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.token_auth_method == "client_secret_post":
            params["client_secret"] = self.client_secret
        elif self.token_auth_method == "client_secret_basic":
            credentials = urllib.parse.quote_plus(self.client_id) + ":" + urllib.parse.quote_plus(self.client_secret)
            headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
        if self.resource: params["resource"] = self.resource
        req = urllib.request.Request(metadata["token_endpoint"], data=urllib.parse.urlencode(params).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response: return json.load(response)

    def callback(self, query, headers):
        state = query.get("state", [""])
        if len(state) != 1: raise AuthError("Invalid login state", 400)
        with self.lock:
            self.prune()
            transaction = self.pending.get(state[0])
            binding = self.read_cookie(headers, self.login_cookie)
            if not transaction or not binding or not hmac.compare_digest(binding, transaction["binding"]):
                raise AuthError("Invalid or expired login state", 400)
            del self.pending[state[0]]
        response_issuer = query.get("iss", [])
        if (response_issuer and (len(response_issuer) != 1 or response_issuer[0] != self.issuer)
                or self.discover().get("authorization_response_iss_parameter_supported") and not response_issuer):
            raise AuthError("Authorization response issuer mismatch", 400)
        if "error" in query: raise AuthError("Login was declined", 400)
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096: raise AuthError("Missing authorization code", 400)
        tokens = self.exchange(codes[0], transaction["verifier"])
        if (not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str)
                or not tokens["access_token"] or str(tokens.get("token_type", "")).lower() != "bearer"):
            raise AuthError("Invalid token endpoint response", 502)
        identity = self.decode(tokens.get("id_token"), self.client_id)
        nonce = identity.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(nonce, transaction["nonce"]):
            raise AuthError("Invalid ID token nonce", 400)
        audience = identity["aud"]
        if (isinstance(audience, list) and len(audience) > 1 and identity.get("azp") != self.client_id
                or "azp" in identity and identity["azp"] != self.client_id):
            raise AuthError("Invalid authorized party", 400)
        expires = min(time.time() + self.session_seconds, identity["exp"])
        if self.admin_claim_source == "access_token":
            access = self.verify_access(tokens.get("access_token"))
            if access["sub"] != identity["sub"]: raise AuthError("Token subject mismatch", 400)
            expires = min(expires, access["exp"])
        else:
            self.require_admin(identity)
        session = {"sub": identity["sub"], "name": identity.get("name") or identity["sub"],
                   "csrf": secrets.token_urlsafe(32), "expires": expires, "id_token_hint": tokens["id_token"]}
        session_id = secrets.token_urlsafe(32)
        with self.lock:
            self.prune()
            if len(self.sessions) >= 10000: raise AuthError("Session capacity exceeded", 429)
            previous = self.read_cookie(headers, self.session_cookie)
            self.sessions.pop(previous, None)
            self.sessions[session_id] = session
        return {key: value for key, value in session.items() if key != "id_token_hint"}, [self.cookie(self.session_cookie, session_id, max(1, int(expires - time.time()))),
                         self.cookie(self.login_cookie, "", 0)]

    def authenticate(self, headers, mutation=False):
        authorization = headers.get("Authorization")
        if authorization:
            if not authorization.startswith("Bearer "): raise AuthError()
            claims = self.verify_access(authorization[7:])
            return {"sub": claims["sub"], "name": claims["sub"], "csrf": None, "expires": claims["exp"]}
        with self.lock:
            self.prune()
            session = self.sessions.get(self.read_cookie(headers, self.session_cookie))
            if not session: raise AuthError("Sign in with your identity provider")
            if mutation:
                if headers.get("Origin") != self.origin:
                    raise AuthError("Same-origin request required", 403)
                csrf = headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(csrf, session["csrf"]):
                    raise AuthError("Invalid CSRF token", 403)
            return {key: value for key, value in session.items() if key != "id_token_hint"}

    def logout(self, headers):
        session = self.authenticate(headers, mutation=True)
        with self.lock:
            stored = self.sessions.pop(self.read_cookie(headers, self.session_cookie), None)
        endpoint = self.discover().get("end_session_endpoint")
        logout_url = self.origin + "/"
        if endpoint:
            logout_url = endpoint + ("&" if urllib.parse.urlsplit(endpoint).query else "?") + urllib.parse.urlencode({"client_id": self.client_id,
                "post_logout_redirect_uri": self.origin + "/",
                **({"id_token_hint": stored["id_token_hint"]} if stored else {})})
        return session, self.cookie(self.session_cookie, "", 0), logout_url
