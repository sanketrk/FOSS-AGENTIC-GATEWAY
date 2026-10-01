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
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from app import Registry, handler
from oidc import OIDCClient, AuthError


class OIDCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.auth = OIDCClient('https://identity.example.com/realms/enterprise', 'client-id', 'private-secret',
                              'https://mcp-control-plane', 'http://127.0.0.1:8080',
                              admin_claim='/entitlements', admin_value='control-plane:admin', admin_claim_source='access_token')
        self.auth.metadata = {'authorization_endpoint': self.auth.issuer + 'authorize',
                              'token_endpoint': self.auth.issuer + '/token',
                              'end_session_endpoint': 'https://login.example.com/logout'}
        self.auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.private_key.public_key()))

    def token(self, audience=None, **claims):
        payload = {'iss': self.auth.issuer, 'aud': audience or self.auth.audience, 'sub': 'user-admin',
                   'iat': int(time.time()), 'exp': int(time.time()) + 600,
                   'entitlements': ['control-plane:admin'], **claims}
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
            'access_token': self.token(), 'token_type': 'Bearer',
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
        self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id, nonce=query['nonce'][0]), 'access_token': self.token(), 'token_type': 'Bearer'}
        identity, _ = self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        self.assertEqual(identity['sub'], 'user-admin')
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, headers)

    def test_reject_expired_state_and_nonce(self):
        query, headers = self.login()
        self.auth.pending[query['state'][0]]['expires'] = time.time() - 1
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        query, headers = self.login()
        self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id, nonce='wrong'), 'access_token': self.token(), 'token_type': 'Bearer'}
        with self.assertRaises(AuthError): self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        self.assertFalse(self.auth.sessions)

    def test_reject_wrong_claims_and_algorithm(self):
        for claims in [{'iss': 'https://attacker/'}, {'aud': 'wrong'}, {'exp': int(time.time()) - 1}, {'sub': ''}]:
            with self.subTest(claims=claims), self.assertRaises(AuthError): self.auth.verify_access(self.token(**claims))
        with self.assertRaises(AuthError):
            self.auth.verify_access(jwt.encode({'sub': 'admin'}, 'attacker-secret' * 4, algorithm='HS256'))
        forged = jwt.encode({'iss': self.auth.issuer, 'aud': self.auth.audience, 'sub': 'admin', 'iat': int(time.time()), 'exp': int(time.time()) + 100}, rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm='RS256')
        with self.assertRaises(AuthError): self.auth.verify_access(forged)

    def test_entitlements_not_scopes_grant_access(self):
        for entitlements in [[], ['other'], None, 'other']:
            with self.subTest(entitlements=entitlements), self.assertRaises(AuthError) as caught:
                self.auth.verify_access(self.token(entitlements=entitlements, scope='control-plane:admin'))
            self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.auth.verify_access(self.token())['sub'], 'user-admin')

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
        self.assertTrue(logout_url.startswith('https://login.example.com/logout?'))
        with self.assertRaises(AuthError): self.auth.authenticate(headers)
        identity, cookies = self.callback()
        self.auth.sessions[next(iter(self.auth.sessions))]['expires'] = time.time() - 1
        with self.assertRaises(AuthError): self.auth.authenticate({'Cookie': cookies[0].split(';')[0]})

    def test_subject_and_authorized_party(self):
        for identity_claims in [{'sub': 'other'}, {'aud': [self.auth.client_id, 'other'], 'azp': 'wrong'}]:
            query, headers = self.login()
            identity = self.token(self.auth.client_id, nonce=query['nonce'][0]) if not identity_claims else self.token(**{'aud': self.auth.client_id, 'nonce': query['nonce'][0], **identity_claims})
            self.auth.exchange = lambda code, verifier: {'id_token': identity, 'access_token': self.token(), 'token_type': 'Bearer'}
            with self.subTest(identity_claims=identity_claims), self.assertRaises(AuthError):
                self.auth.callback({'state': query['state'], 'code': ['code']}, headers)

    def test_secure_cookie_and_configuration(self):
        auth = OIDCClient('https://identity.example.com/', 'id', 'secret', 'api', 'https://control.company.com')
        cookie = auth.cookie(auth.session_cookie, 'opaque', 900)
        for flag in ['__Host-mcp-session', 'Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/']: self.assertIn(flag, cookie)
        for origin in ['http://public.example', 'https://user:pass@example.com', 'https://example.com/path']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                OIDCClient('https://identity.example.com/', 'id', 'secret', 'api', origin)

    def test_discovery_rejects_untrusted_endpoints(self):
        self.auth.metadata = None
        discovery = {'issuer': self.auth.issuer, 'authorization_endpoint': 'http://insecure.example/authorize',
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
            self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id, nonce=query['nonce'][0]), 'access_token': self.token(), 'token_type': 'Bearer'}
            code, headers, _ = request('/auth/callback?' + urllib.parse.urlencode({'state': query['state'][0], 'code': 'auth-code'}), cookie)
            self.assertEqual(code, 302)
            self.assertEqual(headers['Location'], '/')
            cookies = headers.get_all('Set-Cookie')
            session_cookie = next(c for c in cookies if c.startswith(self.auth.session_cookie + '='))
            self.assertIn('HttpOnly', session_cookie)
            code, _, body = request('/api/session', session_cookie.split(';')[0])
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)['sub'], 'user-admin')
            self.assertNotIn('id_token_hint', json.loads(body))
            self.assertEqual(registry.status()['events'][0]['action'], 'login')
        finally:
            server.shutdown(); server.server_close(); thread.join(); registry.db.close()

    def test_default_profile_uses_id_claim_and_allows_opaque_access_token(self):
        self.auth.admin_claim_source = 'id_token'
        self.auth.admin_claim = '/realm_access/roles'
        self.auth.admin_value = 'mcp-admin'
        query, headers = self.login()
        self.assertNotIn('audience', query)
        self.assertEqual(query['scope'], ['openid profile email'])
        self.auth.exchange = lambda code, verifier: {'id_token': self.token(self.auth.client_id,
            nonce=query['nonce'][0], realm_access={'roles': ['mcp-admin']}),
            'access_token': 'opaque-token', 'token_type': 'Bearer'}
        identity, _ = self.auth.callback({'state': query['state'], 'code': ['code']}, headers)
        self.assertEqual(identity['sub'], 'user-admin')
        self.auth.audience = None
        with self.assertRaises(AuthError): self.auth.verify_access(self.token())

    def test_json_pointer_namespaced_claim_and_no_scope_authorization(self):
        self.auth.admin_claim = '/https:~1~1claims.example.com~1roles'
        self.auth.admin_value = 'mcp-admin'
        self.auth.require_admin({'https://claims.example.com/roles': ['mcp-admin']})
        with self.assertRaises(AuthError): self.auth.require_admin({'scope': 'mcp-admin'})
        self.auth.admin_claim = '/groups/0/role'
        self.auth.require_admin({'groups': [{'role': 'mcp-admin'}]})

    def test_discovery_supports_path_issuers_and_separate_endpoint_hosts(self):
        self.auth.metadata = None
        discovery = {'issuer': self.auth.issuer,
            'authorization_endpoint': 'https://login.example.com/authorize',
            'token_endpoint': 'https://tokens.example.com/token', 'jwks_uri': 'https://keys.example.com/jwks',
            'response_types_supported': ['code'], 'id_token_signing_alg_values_supported': ['RS256'],
            'end_session_endpoint': 'https://login.example.com/logout'}
        with patch('oidc.urllib.request.urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = json.dumps(discovery).encode()
            result = self.auth.discover()
            self.assertEqual(result['token_endpoint'], 'https://tokens.example.com/token')
            self.assertEqual(fetch.call_args.args[0], 'https://identity.example.com/realms/enterprise/.well-known/openid-configuration')

    def test_standard_token_endpoint_authentication_and_resource(self):
        for method in ['client_secret_basic', 'client_secret_post', 'none']:
            self.auth.token_auth_method = method
            self.auth.resource = 'https://control.example.com'
            with self.subTest(method=method), patch('oidc.urllib.request.urlopen') as fetch:
                fetch.return_value.__enter__.return_value.read.return_value = b'{}'
                self.auth.exchange('code', 'verifier')
                request = fetch.call_args.args[0]
                body = urllib.parse.parse_qs(request.data.decode())
                self.assertEqual(body['resource'], ['https://control.example.com'])
                self.assertEqual(body['code_verifier'], ['verifier'])
                if method == 'client_secret_basic':
                    self.assertTrue(request.headers['Authorization'].startswith('Basic '))
                    self.assertNotIn('client_secret', body)
                elif method == 'client_secret_post': self.assertEqual(body['client_secret'], ['private-secret'])
                else:
                    self.assertNotIn('Authorization', request.headers)
                    self.assertNotIn('client_secret', body)

    def test_authorization_issuer_validation_precedes_token_exchange(self):
        calls = []
        self.auth.exchange = lambda *args: calls.append(args)
        for advertisement, response_issuer in [(True, None), (False, 'https://wrong.example.com/')]:
            self.auth.metadata['authorization_response_iss_parameter_supported'] = advertisement
            query, headers = self.login()
            callback = {'state': query['state'], 'code': ['code']}
            if response_issuer: callback['iss'] = [response_issuer]
            with self.subTest(advertisement=advertisement), self.assertRaises(AuthError): self.auth.callback(callback, headers)
        self.assertFalse(calls)

    def test_extensions_cannot_override_protocol_fields(self):
        for params in [{'state': 'injected'}, {'redirect_uri': 'https://attacker'}, {'client_secret': 'secret'}]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                OIDCClient('https://identity.example.com/', 'id', 'secret', None, 'https://control.example.com', authorization_params=params)
        self.auth.authorization_params = {'audience': 'provider-example'}
        query, _ = self.login()
        self.assertEqual(query['audience'], ['provider-example'])

    def test_provider_without_logout_endpoint_uses_local_logout(self):
        self.auth.metadata.pop('end_session_endpoint')
        identity, cookies = self.callback()
        _, _, location = self.auth.logout({'Cookie': cookies[0].split(';')[0],
            'Origin': self.auth.origin, 'X-CSRF-Token': identity['csrf']})
        self.assertEqual(location, self.auth.origin + '/')

    def test_configured_ec_signature_algorithm(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        self.auth.signing_algorithms = ('ES256',)
        self.auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private_key.public_key()))
        token = jwt.encode({'iss': self.auth.issuer, 'aud': self.auth.audience, 'sub': 'user-admin',
            'iat': int(time.time()), 'exp': int(time.time()) + 300,
            'entitlements': ['control-plane:admin']}, private_key, algorithm='ES256')
        self.assertEqual(self.auth.verify_access(token)['sub'], 'user-admin')
        self.auth.signing_algorithms = ('RS256',)
        with self.assertRaises(AuthError): self.auth.verify_access(token)

    def test_endpoint_query_parameters_are_preserved(self):
        self.auth.metadata['authorization_endpoint'] = 'https://login.example.com/authorize?realm=enterprise'
        self.auth.metadata['end_session_endpoint'] = 'https://login.example.com/logout?realm=enterprise'
        query, _ = self.login()
        self.assertEqual(query['realm'], ['enterprise'])
        identity, cookies = self.callback()
        _, _, location = self.auth.logout({'Cookie': cookies[0].split(';')[0],
            'Origin': self.auth.origin, 'X-CSRF-Token': identity['csrf']})
        logout_query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        self.assertEqual(logout_query['realm'], ['enterprise'])
        self.assertEqual(logout_query['post_logout_redirect_uri'], [self.auth.origin + '/'])

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
            self.assertEqual(request('/api/session', headers=bearer)[2]['sub'], 'user-admin')
            self.assertEqual(request('/api/servers', headers={'Authorization': 'Bearer ' + self.token(entitlements=[])})[0], 403)
            identity, cookies = self.callback()
            headers = {'Cookie': cookies[0].split(';')[0], 'Content-Type': 'application/json'}
            self.assertEqual(request('/api/servers', 'POST', headers, b'{}')[0], 403)
            from test_app import sample
            headers.update({'Origin': self.auth.origin, 'X-CSRF-Token': identity['csrf']})
            self.assertEqual(request('/api/servers', 'POST', headers, json.dumps(sample()).encode())[0], 201)
            self.assertEqual(registry.status()['events'][0]['details']['actor'], 'user-admin')
        finally:
            server.shutdown(); server.server_close(); thread.join(); registry.db.close()


if __name__ == '__main__': unittest.main()
