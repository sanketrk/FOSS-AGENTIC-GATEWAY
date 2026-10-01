import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from app import Registry, handler
from oidc import Auth0OIDC, AuthError


class OIDCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.auth = Auth0OIDC('dev-example.us.auth0.com', 'client-id', 'private-secret',
                              'https://mcp-control-plane', 'http://127.0.0.1:8080')
        self.auth.metadata = {'authorization_endpoint': self.auth.issuer + 'authorize',
                              'token_endpoint': self.auth.issuer + 'oauth/token'}
        self.auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.private_key.public_key()))

    def token(self, audience=None, **claims):
        payload = {'iss': self.auth.issuer, 'aud': audience or self.auth.audience, 'sub': 'auth0|admin',
                   'iat': int(time.time()), 'exp': int(time.time()) + 600,
                   'permissions': ['control-plane:admin'], **claims}
        return jwt.encode(payload, self.private_key, algorithm='RS256', headers={'kid': 'test'})

    def login(self):
        location, cookie = self.auth.login()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        parsed = SimpleCookie(cookie)
        headers = {'Cookie': self.auth.login_cookie + '=' + parsed[self.auth.login_cookie].value}
        return query, headers

    def callback(self, **patches):
        query, headers = self.login()
        self.auth.exchange = lambda code, verifier: {
            'id_token': self.token(self.auth.client_id, nonce=query['nonce'][0], name='Admin', **patches),
            'access_token': self.token(),
        }
        identity, cookies = self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        return identity, cookies

    def test_pkce_state_and_nonce(self):
        query, headers = self.login()
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['redirect_uri'], ['http://127.0.0.1:8080/auth/callback'])
        self.assertNotIn('client_secret', query)
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, {})
        # A failed browser binding must not consume another browser's pending login.
        self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id, nonce=query['nonce'][0]), 'access_token': self.token()}
        identity, _ = self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        self.assertEqual(identity['sub'], 'auth0|admin')
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, headers)

    def test_reject_expired_state_and_nonce(self):
        query, headers = self.login()
        self.auth.pending[query['state'][0]]['expires'] = time.time() - 1
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        query, headers = self.login()
        self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id, nonce='wrong'), 'access_token': self.token()}
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        self.assertFalse(self.auth.sessions)

    def test_reject_wrong_claims_and_algorithm(self):
        for claims in [{'iss': 'https://attacker/'}, {'aud': 'wrong'}, {'exp': int(time.time()) - 1}, {'sub': ''}]:
            with self.subTest(claims=claims), self.assertRaises(AuthError): self.auth.verify_access(self.token(**claims))
        with self.assertRaises(AuthError):
            self.auth.verify_access(jwt.encode({'sub': 'admin'}, 'attacker-secret' * 4, algorithm='HS256'))
        forged = jwt.encode({'iss': self.auth.issuer, 'aud': self.auth.audience, 'sub': 'admin', 'iat': int(time.time()), 'exp': int(time.time()) + 100}, rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm='RS256')
        with self.assertRaises(AuthError): self.auth.verify_access(forged)

    def test_permissions_not_scopes_grant_access(self):
        for permissions in [[], ['other'], None, 'control-plane:admin']:
            with self.subTest(permissions=permissions), self.assertRaises(AuthError) as caught:
                self.auth.verify_access(self.token(permissions=permissions, scope='control-plane:admin'))
            self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.auth.verify_access(self.token())['sub'], 'auth0|admin')

    def test_session_csrf_expiry_and_logout(self):
        identity, cookies = self.callback()
        parsed = SimpleCookie(cookies[0])
        headers = {'Cookie': self.auth.session_cookie + '=' + parsed[self.auth.session_cookie].value}
        self.assertEqual(self.auth.authenticate(headers)['sub'], identity['sub'])
        with self.assertRaises(AuthError): self.auth.authenticate(headers, mutation=True)
        headers.update({'Origin': self.auth.origin, 'X-CSRF-Token': identity['csrf']})
        self.auth.authenticate(headers, mutation=True)
        with self.assertRaises(AuthError): self.auth.authenticate({**headers, 'Origin': 'https://attacker'}, mutation=True)
        _, cleared, logout_url = self.auth.logout(headers)
        self.assertIn('Max-Age=0', cleared)
        self.assertTrue(logout_url.startswith(self.auth.issuer + 'v2/logout?'))
        with self.assertRaises(AuthError): self.auth.authenticate(headers)
        identity, cookies = self.callback()
        self.auth.sessions[next(iter(self.auth.sessions))]['expires'] = time.time() - 1
        with self.assertRaises(AuthError): self.auth.authenticate({'Cookie': cookies[0].split(';')[0]})

    def test_subject_and_authorized_party(self):
        for identity_claims in [{'sub': 'other'}, {'aud': [self.auth.client_id, 'other'], 'azp': 'wrong'}]:
            query, headers = self.login()
            identity = self.token(self.auth.client_id, nonce=query['nonce'][0]) if not identity_claims else self.token(**{'aud': self.auth.client_id, 'nonce': query['nonce'][0], **identity_claims})
            self.auth.exchange = lambda code, verifier: {'id_token': identity, 'access_token': self.token()}
            with self.subTest(identity_claims=identity_claims), self.assertRaises(AuthError):
                self.auth.callback({'state': query['state'], 'code': ['code']}, headers)

    def test_secure_cookie_and_configuration(self):
        auth = Auth0OIDC('example.auth0.com', 'id', 'secret', 'api', 'https://control.company.com')
        cookie = auth.cookie(auth.session_cookie, 'opaque', 900)
        for flag in ['__Host-mcp-session', 'Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/']: self.assertIn(flag, cookie)
        for origin in ['http://public.example', 'https://user:pass@example.com', 'https://example.com/path']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Auth0OIDC('example.auth0.com', 'id', 'secret', 'api', origin)

    def test_discovery_rejects_untrusted_endpoints(self):
        self.auth.metadata = None
        discovery = {'issuer': self.auth.issuer, 'authorization_endpoint': 'https://attacker.example/authorize',
                     'token_endpoint': self.auth.issuer + 'oauth/token', 'jwks_uri': self.auth.issuer + '.well-known/jwks.json'}
        with patch('oidc.urllib.request.urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = json.dumps(discovery).encode()
            with self.assertRaises(AuthError): self.auth.discover()

    def test_http_login_callback_creates_cookie_session(self):
        registry = Registry(':memory:', 'https://gateway.example.com')
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(registry, oidc=self.auth))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        opener = urllib.request.build_opener(NoRedirect())
        def request(path, cookie=''):
            try: response = opener.open(urllib.request.Request(base + path, headers={'Cookie': cookie}), timeout=3)
            except urllib.error.HTTPError as error: response = error
            with response: return response.status, response.headers, response.read()
        try:
            code, headers, _ = request('/auth/login')
            self.assertEqual(code, 302)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(headers['Location']).query)
            cookie = headers['Set-Cookie'].split(';')[0]
            self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id, nonce=query['nonce'][0]), 'access_token': self.token()}
            code, headers, _ = request('/auth/callback?' + urllib.parse.urlencode({'state': query['state'][0], 'code': 'auth-code'}), cookie)
            self.assertEqual(code, 302)
            self.assertEqual(headers['Location'], '/')
            cookies = headers.get_all('Set-Cookie')
            session_cookie = next(c for c in cookies if c.startswith(self.auth.session_cookie + '='))
            self.assertIn('HttpOnly', session_cookie)
            code, _, body = request('/api/session', session_cookie.split(';')[0])
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)['sub'], 'auth0|admin')
            self.assertEqual(registry.status()['events'][0]['action'], 'login')
        finally:
            server.shutdown(); server.server_close(); thread.join(); registry.db.close()

    def test_api_session_and_bearer_and_audit(self):
        registry = Registry(':memory:', 'https://gateway.example.com')
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(registry, oidc=self.auth))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        def request(path, method='GET', headers=None, data=None):
            req = urllib.request.Request(base + path, method=method, headers=headers or {}, data=data)
            try: response = urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError as error: response = error
            with response: return response.status, response.headers, json.loads(response.read())
        try:
            self.assertEqual(request('/auth/config')[2]['mode'], 'oidc')
            self.assertEqual(request('/api/servers')[0], 401)
            self.assertEqual(request('/api/servers', headers={'Authorization': 'Bearer old-admin-token'})[0], 401)
            bearer = {'Authorization': 'Bearer ' + self.token()}
            self.assertEqual(request('/api/session', headers=bearer)[2]['sub'], 'auth0|admin')
            self.assertEqual(request('/api/servers', headers={'Authorization': 'Bearer ' + self.token(permissions=[])})[0], 403)
            identity, cookies = self.callback()
            headers = {'Cookie': cookies[0].split(';')[0], 'Content-Type': 'application/json'}
            self.assertEqual(request('/api/servers', 'POST', headers, b'{}')[0], 403)
            from test_app import sample
            headers.update({'Origin': self.auth.origin, 'X-CSRF-Token': identity['csrf']})
            self.assertEqual(request('/api/servers', 'POST', headers, json.dumps(sample()).encode())[0], 201)
            self.assertEqual(registry.status()['events'][0]['details']['actor'], 'auth0|admin')
        finally:
            server.shutdown(); server.server_close(); thread.join(); registry.db.close()


if __name__ == '__main__': unittest.main()
