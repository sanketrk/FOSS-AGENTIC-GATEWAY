"""Auth0 authorization code login with PKCE and server-side administrator sessions."""
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


class Auth0OIDC:
    def __init__(self, domain, client_id, client_secret, audience, origin,
                 permission="control-plane:admin", session_seconds=900):
        if not isinstance(domain, str) or not domain or not all(part and part[0] != "-" and part[-1] != "-" for part in domain.split(".")):
            raise ValueError("AUTH0_DOMAIN must be a hostname")
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-." for c in domain):
            raise ValueError("AUTH0_DOMAIN must be a hostname without a URL scheme")
        if not all(isinstance(v, str) and v for v in (client_id, client_secret, audience, permission)):
            raise ValueError("Auth0 client ID, client secret, API audience and admin permission are required")
        self.issuer = "https://" + domain.lower() + "/"
        self.origin = public_origin(origin)
        self.client_id, self.client_secret, self.audience = client_id, client_secret, audience
        self.permission = permission
        self.session_seconds = max(60, min(int(session_seconds), 3600))
        self.secure = self.origin.startswith("https://")
        self.session_cookie = "__Host-mcp-session" if self.secure else "mcp-session"
        self.login_cookie = "__Host-mcp-login" if self.secure else "mcp-login"
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
            with urllib.request.urlopen(self.issuer + ".well-known/openid-configuration", timeout=10) as response:
                metadata = json.load(response)
            if metadata.get("issuer") != self.issuer:
                raise AuthError("OIDC discovery issuer mismatch", 502)
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                endpoint = urllib.parse.urlsplit(metadata.get(field, ""))
                issuer = urllib.parse.urlsplit(self.issuer)
                if endpoint.scheme != "https" or endpoint.netloc != issuer.netloc or endpoint.username or endpoint.fragment or endpoint.query:
                    raise AuthError("Untrusted OIDC discovery endpoint", 502)
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
        query = urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "response_mode": "query",
            "redirect_uri": self.origin + "/auth/callback", "scope": "openid profile email " + self.permission,
            "audience": self.audience, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return metadata["authorization_endpoint"] + "?" + query, self.cookie(self.login_cookie, binding, 300)

    def decode(self, token, audience):
        if not isinstance(token, str) or len(token) > 32768:
            raise AuthError()
        self.discover()
        try:
            if jwt.get_unverified_header(token).get("alg") != "RS256": raise AuthError()
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=self.issuer,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]: raise AuthError()
            return claims
        except jwt.PyJWTError:
            raise AuthError("Invalid or expired token") from None

    def verify_access(self, token):
        claims = self.decode(token, self.audience)
        permissions = claims.get("permissions")
        # Auth0 RBAC permissions, not a user-requested scope, grant admin access.
        if not isinstance(permissions, list) or self.permission not in permissions:
            raise AuthError("Administrator permission required", 403)
        return claims

    def exchange(self, code, verifier):
        metadata = self.discover()
        payload = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
            "redirect_uri": self.origin + "/auth/callback", "client_id": self.client_id,
            "client_secret": self.client_secret, "code_verifier": verifier}).encode()
        req = urllib.request.Request(metadata["token_endpoint"], data=payload,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
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
        if "error" in query: raise AuthError("Login was declined", 400)
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096: raise AuthError("Missing authorization code", 400)
        tokens = self.exchange(codes[0], transaction["verifier"])
        identity = self.decode(tokens.get("id_token"), self.client_id)
        nonce = identity.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(nonce, transaction["nonce"]):
            raise AuthError("Invalid ID token nonce", 400)
        audience = identity["aud"]
        if (isinstance(audience, list) and len(audience) > 1 and identity.get("azp") != self.client_id
                or "azp" in identity and identity["azp"] != self.client_id):
            raise AuthError("Invalid authorized party", 400)
        access = self.verify_access(tokens.get("access_token"))
        if access["sub"] != identity["sub"]: raise AuthError("Token subject mismatch", 400)
        expires = min(time.time() + self.session_seconds, identity["exp"], access["exp"])
        session = {"sub": identity["sub"], "name": identity.get("name") or identity["sub"],
                   "csrf": secrets.token_urlsafe(32), "expires": expires}
        session_id = secrets.token_urlsafe(32)
        with self.lock:
            self.prune()
            if len(self.sessions) >= 10000: raise AuthError("Session capacity exceeded", 429)
            previous = self.read_cookie(headers, self.session_cookie)
            self.sessions.pop(previous, None)
            self.sessions[session_id] = session
        return session, [self.cookie(self.session_cookie, session_id, max(1, int(expires - time.time()))),
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
            if not session: raise AuthError("Sign in with Auth0")
            if mutation:
                if headers.get("Origin") != self.origin:
                    raise AuthError("Same-origin request required", 403)
                csrf = headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(csrf, session["csrf"]):
                    raise AuthError("Invalid CSRF token", 403)
            return dict(session)

    def logout(self, headers):
        session = self.authenticate(headers, mutation=True)
        with self.lock: self.sessions.pop(self.read_cookie(headers, self.session_cookie), None)
        logout_url = self.issuer + "v2/logout?" + urllib.parse.urlencode({"client_id": self.client_id, "returnTo": self.origin + "/"})
        return session, self.cookie(self.session_cookie, "", 0), logout_url
