"""Security boundaries and protocol lifecycle of the shared gateway SDK."""
import json
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from foss_agentic_gateway import AuthenticationError, Endpoint, ExchangeTokenVerifier, GatewayClient, GatewayError


class ExchangeVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        key.update(kid='sdk-test', use='sig', alg='RS256')
        cls.jwks = {'keys': [key]}

    def setUp(self):
        self.verifier = ExchangeTokenVerifier(issuer='https://issuer.example/', audience='urn:backend:accounts',
            scopes=('accounts:summary',), gateway_actor='authorized-gateway', subjects=('caller-agent',), jwks=self.jwks)

    def token(self, **changes):
        now = int(time.time())
        claims = {'iss': 'https://issuer.example/', 'sub': 'caller-agent', 'aud': 'urn:backend:accounts',
                  'scope': 'accounts:summary', 'act': {'sub': 'authorized-gateway'}, 'iat': now, 'exp': now + 120}
        claims.update(changes)
        return jwt.encode(claims, self.private, algorithm='RS256', headers={'kid': 'sdk-test'})

    def test_accepts_signed_exchange_policy_without_demo_custom_claim(self):
        result = self.verifier.verify('Bearer ' + self.token())
        self.assertEqual(result['act']['sub'], 'authorized-gateway')
        self.assertNotIn('token_use', result)  # This claim is not an SDK requirement or an OAuth standard.

    def test_rejects_original_wrong_backend_and_missing_gateway_actor(self):
        for changes in ({'aud': 'https://gateway.example/mcp/accounts', 'act': None},
                        {'aud': 'urn:backend:transactions'}, {'act': None},
                        {'act': {'sub': 'unapproved-gateway'}}, {'sub': 'unapproved-caller'}):
            with self.subTest(changes=changes), self.assertRaises(AuthenticationError):
                self.verifier.verify('Bearer ' + self.token(**changes))

    def test_rejects_wrong_issuer_expiry_timestamps_and_scopes(self):
        for changes in ({'iss': 'https://other-issuer.example/'}, {'exp': int(time.time()) - 1},
                        {'exp': None}, {'exp': '9999999999'}, {'scope': 'accounts:read'},
                        {'iat': int(time.time()) + 3600}):
            with self.subTest(changes=changes), self.assertRaises(AuthenticationError):
                self.verifier.verify('Bearer ' + self.token(**changes))

    def test_rejects_forged_unsigned_and_malformed_tokens(self):
        for value in (None, 'Bearer', 'Basic abc', 'Bearer malformed', 'Bearer ' + 'x' * 32769):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(AuthenticationError):
                self.verifier.verify(value)
        payload = jwt.decode(self.token(), options={'verify_signature': False})
        forged = jwt.encode(payload, rsa.generate_private_key(public_exponent=65537, key_size=2048),
                            algorithm='RS256', headers={'kid': 'sdk-test'})
        symmetric = jwt.encode(payload, 'not-a-trusted-key-' * 3, algorithm='HS256', headers={'kid': 'sdk-test'})
        for token in (forged, symmetric):
            with self.assertRaises(AuthenticationError): self.verifier.verify('Bearer ' + token)

    def test_configuration_cannot_disable_exchange_policy(self):
        for changed in ({'gateway_actor': ''}, {'scopes': ()}, {'algorithms': ('HS256',)}):
            kwargs = dict(issuer='https://issuer.example/', audience='urn:backend:accounts',
                          scopes=('accounts:summary',), gateway_actor='gateway', jwks=self.jwks)
            kwargs.update(changed)
            with self.assertRaises(ValueError): ExchangeTokenVerifier(**kwargs)


class GatewayClientTests(unittest.TestCase):
    def setUp(self):
        self.grants = []
        class Provider:
            def get_token(inner, audience, scopes):
                self.grants.append((audience, scopes)); return 'gateway-access-token'
        self.provider = Provider()
        self.client = GatewayClient(gateway_url='https://gateway.example', token_provider=self.provider)

    def test_rejects_http_and_direct_backend_endpoint_paths(self):
        with self.assertRaises(ValueError): GatewayClient(gateway_url='http://gateway.example', token_provider=self.provider)
        for path in ('https://backend.example/rpc', '//backend.example/rpc', '/../rpc', '/rpc?redirect=other', '/rpc%2fextra'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                Endpoint(path=path, audience='agent', scopes=('call',), protocol='a2a')

    def test_mcp_lifecycle_is_gateway_only_and_resource_bound(self):
        endpoint = Endpoint(path='/mcp/accounts', audience='https://gateway.example/mcp/accounts',
                            scopes=('accounts:read',), protocol='mcp')
        methods = []
        def request(url, data=None, headers=None):
            self.assertTrue(url.startswith('https://gateway.example/'))
            if data is None: return 200, {'resource': endpoint.audience}
            self.assertEqual(headers['Authorization'], 'Bearer gateway-access-token')
            message = json.loads(data); methods.append(message['method'])
            results = {'initialize': {'protocolVersion': '2025-11-25'},
                       'tools/list': {'tools': [{'name': 'summary'}]}, 'tools/call': {'content': [], 'isError': False}}
            if 'id' not in message: return 202, None
            return 200, {'jsonrpc': '2.0', 'id': message['id'], 'result': results[message['method']]}
        with patch.object(self.client.transport, 'request', side_effect=request):
            self.client.call_tool(endpoint, 'summary', {'account_id': 'demo'})
        self.assertEqual(methods, ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])
        self.assertEqual(self.grants, [(endpoint.audience, endpoint.scopes)])

    def test_mcp_audience_mismatch_is_rejected_before_token_acquisition(self):
        endpoint = Endpoint(path='/mcp/accounts', audience='urn:backend:accounts', scopes=('read',), protocol='mcp')
        with self.assertRaises(ValueError): self.client.call_tool(endpoint, 'summary', {})
        self.assertEqual(self.grants, [])

    def test_a2a_uses_gateway_token_and_checks_rpc_correlation(self):
        endpoint = Endpoint(path='/a2a/review', audience='urn:gateway:review', scopes=('review',), protocol='a2a')
        def response(url, data, headers):
            self.assertEqual(url, 'https://gateway.example/a2a/review')
            self.assertEqual(headers['A2A-Version'], '1.0')
            self.assertEqual(json.loads(data)['params']['message']['role'], 'ROLE_USER')
            return 200, {'jsonrpc': '2.0', 'id': 'wrong-response-id', 'result': {'message': {}}}
        with patch.object(self.client.transport, 'request', side_effect=response), self.assertRaises(GatewayError):
            self.client.send_message(endpoint, 'review')


if __name__ == '__main__': unittest.main()
