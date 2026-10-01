"""Exercise the banking fixtures with real signatures and verified local TLS."""
import base64
import json
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import jwt

from banking_demo.config import (AGENT_ID, EXCHANGE_ID, ISSUER, POLICIES, ACCESS_TOKEN, EXCHANGE_GRANT)
from foss_agentic_gateway import GatewayError, OAuthClientCredentials
from banking_demo.prepare import prepare
from banking_demo.issuer import handler as issuer_handler, public_key, issue
from review_agent import handler as review_handler
from accounts_server import handler as accounts_handler
from transactions_server import handler as transactions_handler


class BankingFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        with patch('builtins.print'): prepare(cls.root)
        cls.context = ssl.create_default_context(cafile=str(cls.root / 'client' / 'ca.pem'))
        cls.servers, cls.threads, cls.urls = [], [], {}
        for kind in ('issuer', 'review', 'accounts', 'transactions'):
            directory = cls.root / ('issuer' if kind == 'issuer' else 'backend')
            factory = {'issuer': issuer_handler, 'review': review_handler, 'accounts': accounts_handler, 'transactions': transactions_handler}[kind]
            server = ThreadingHTTPServer(('127.0.0.1', 0), factory(directory))
            if kind == 'issuer':
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(directory / 'tls.pem'), str(directory / 'tls.key'))
                server.socket = context.wrap_socket(server.socket, server_side=True)
            cls.urls[kind] = ('https' if kind == 'issuer' else 'http') + f'://localhost:{server.server_port}'
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            cls.servers.append(server); cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers: server.shutdown(); server.server_close()
        for thread in cls.threads: thread.join()
        cls.temporary.cleanup()

    def request(self, kind, path, data=None, headers=None):
        req = urllib.request.Request(self.urls[kind] + path, data=data, headers=headers or {})
        try: response = urllib.request.urlopen(req, context=self.context, timeout=3)
        except urllib.error.HTTPError as error: response = error
        with response:
            body = response.read()
            return response.status, json.loads(body) if body else None

    def token_request(self, form, client=AGENT_ID):
        filename = 'agent-secret' if client == AGENT_ID else 'exchange-secret'
        secret = (self.root / 'issuer' / filename).read_text()
        auth = base64.b64encode((client + ':' + secret).encode()).decode()
        return self.request('issuer', '/token', urllib.parse.urlencode(form).encode(),
                            {'Authorization': 'Basic ' + auth, 'Content-Type': 'application/x-www-form-urlencoded'})

    def gateway_token(self, name):
        p = POLICIES[name]
        status, data = self.token_request({'grant_type': 'client_credentials', 'resource': p['audience'], 'scope': p['gateway_scope']})
        self.assertEqual(status, 200)
        return data['access_token']

    def exchange_form(self, name, token):
        p = POLICIES[name]
        return {'grant_type': EXCHANGE_GRANT, 'subject_token': token, 'subject_token_type': ACCESS_TOKEN,
                'requested_token_type': ACCESS_TOKEN, 'resource': p['resource'], 'scope': p['backend_scope']}

    def upstream_token(self, name):
        status, data = self.token_request(self.exchange_form(name, self.gateway_token(name)), EXCHANGE_ID)
        self.assertEqual(status, 200)
        return data['access_token']

    def rpc(self, name, token, method, params, notification=False):
        body = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notification: body['id'] = 1
        return self.request(name, '/rpc' if name == 'review' else '/mcp', json.dumps(body).encode(),
                            {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'A2A-Version': '1.0'})

    def test_exchange_rebinds_audience_scopes_and_preserves_subject(self):
        for name, p in POLICIES.items():
            with self.subTest(name=name):
                claims = jwt.decode(self.upstream_token(name), public_key(self.root / 'backend'), algorithms=['RS256'],
                                    issuer=ISSUER, audience=p['resource'])
                self.assertEqual(claims['scope'], p['backend_scope'])
                self.assertEqual(claims['sub'], AGENT_ID)
                self.assertEqual(claims['act'], {'sub': EXCHANGE_ID})
                self.assertEqual(claims['token_use'], 'upstream')

    def test_exchange_limits_broad_subject_to_one_target_scope(self):
        # This fixture signs a broad subject as the trusted issuer; production
        # callers never receive the signing key. It is not an end-user login flow.
        signing_key = (self.root / 'issuer' / 'signing.key').read_bytes()
        verification_key = public_key(self.root / 'backend')
        unrelated_scopes = [f'unrelated:{index}' for index in range(99)]
        for name, policy in POLICIES.items():
            with self.subTest(name=name):
                broad_token = issue(signing_key, policy['audience'],
                                    ' '.join([policy['gateway_scope'], *unrelated_scopes]))
                original = jwt.decode(broad_token, verification_key, algorithms=['RS256'],
                                      issuer=ISSUER, audience=policy['audience'])
                self.assertEqual(len(original['scope'].split()), 100)
                status, data = self.token_request(self.exchange_form(name, broad_token), EXCHANGE_ID)
                self.assertEqual(status, 200)
                exchanged = jwt.decode(data['access_token'], verification_key, algorithms=['RS256'],
                                       issuer=ISSUER, audience=policy['resource'])
                self.assertEqual(exchanged['scope'].split(), [policy['backend_scope']])
                self.assertEqual(data['scope'], policy['backend_scope'])
                self.assertEqual(exchanged['sub'], original['sub'])
                self.assertEqual(exchanged['act']['sub'], EXCHANGE_ID)
                unentitled = issue(signing_key, policy['audience'], ' '.join(unrelated_scopes))
                status, _ = self.token_request(self.exchange_form(name, unentitled), EXCHANGE_ID)
                self.assertEqual(status, 400)

    def test_sts_rejects_caller_credentials_cross_audience_and_expired_subject(self):
        token = self.gateway_token('accounts')
        self.assertEqual(self.token_request(self.exchange_form('accounts', token))[0], 401)
        self.assertEqual(self.token_request(self.exchange_form('transactions', token), EXCHANGE_ID)[0], 400)
        claims = jwt.decode(token, options={'verify_signature': False})
        claims['exp'] = int(time.time()) - 1
        expired = jwt.encode(claims, (self.root / 'issuer' / 'signing.key').read_bytes(), algorithm='RS256', headers={'kid': 'banking-demo'})
        self.assertEqual(self.token_request(self.exchange_form('accounts', expired), EXCHANGE_ID)[0], 400)
        form = self.exchange_form('accounts', token); form['scope'] += ' payments:write'
        self.assertEqual(self.token_request(form, EXCHANGE_ID)[0], 400)

    def test_backend_rejects_original_and_other_backend_tokens(self):
        self.assertEqual(self.rpc('accounts', self.gateway_token('accounts'), 'tools/list', {})[0], 401)
        self.assertEqual(self.rpc('transactions', self.upstream_token('accounts'), 'tools/list', {})[0], 401)

    def test_mcp_lifecycle_and_account_boundary(self):
        for name in ('accounts', 'transactions'):
            with self.subTest(name=name):
                token = self.upstream_token(name)
                status, result = self.rpc(name, token, 'initialize', {'protocolVersion': '2025-11-25'})
                self.assertEqual((status, result['result']['protocolVersion']), (200, '2025-11-25'))
                self.assertEqual(self.rpc(name, token, 'notifications/initialized', {}, notification=True), (202, None))
                _, result = self.rpc(name, token, 'tools/list', {})
                tool = result['result']['tools'][0]['name']
                _, result = self.rpc(name, token, 'tools/call', {'name': tool, 'arguments': {'account_id': 'DEMO-001'}})
                data = json.loads(result['result']['content'][0]['text'])
                self.assertTrue(data['synthetic'])
                self.assertEqual(data['verified_upstream_identity']['audience'], POLICIES[name]['resource'])
                _, result = self.rpc(name, token, 'tools/call', {'name': tool, 'arguments': {'account_id': 'OTHER'}})
                self.assertTrue(result['result']['isError'])

    def test_a2a_card_and_message(self):
        status, card = self.request('review', '/.well-known/agent-card.json')
        self.assertEqual(status, 200)
        self.assertEqual(card['supportedInterfaces'][0]['protocolVersion'], '1.0')
        message = {'message': {'messageId': 'demo-message', 'role': 'ROLE_USER', 'parts': [{'text': 'Review DEMO-TX-003'}]}}
        status, response = self.rpc('review', self.upstream_token('review'), 'SendMessage', message)
        self.assertEqual(status, 200)
        self.assertEqual(response['result']['message']['role'], 'ROLE_AGENT')
        self.assertEqual(response['result']['message']['metadata']['verified_upstream_identity']['actor'], EXCHANGE_ID)

    def test_rest_message_requires_exchanged_token(self):
        message = {'message': {'messageId': 'rest-demo', 'role': 'ROLE_USER', 'parts': [{'text': 'Review'}]}}
        headers = {'Content-Type': 'application/json', 'A2A-Version': '1.0',
                   'Authorization': 'Bearer ' + self.upstream_token('review')}
        status, response = self.request('review', '/message:send', json.dumps(message).encode(), headers)
        self.assertEqual(status, 200)
        self.assertNotIn('jsonrpc', response)
        self.assertEqual(response['message']['metadata']['verified_upstream_identity']['actor'], EXCHANGE_ID)
        headers.pop('Authorization')
        status, _ = self.request('review', '/message:send', json.dumps(message).encode(), headers)
        self.assertEqual(status, 401)

    def test_sdk_token_acquisition_and_redirect_rejection(self):
        provider = OAuthClientCredentials(token_endpoint=self.urls['issuer'] + '/token', client_id=AGENT_ID,
            client_secret=(self.root / 'issuer' / 'agent-secret').read_text(), ca_file=str(self.root / 'client' / 'ca.pem'))
        p = POLICIES['accounts']
        token = provider.get_token(p['audience'], (p['gateway_scope'],))
        claims = jwt.decode(token, public_key(self.root / 'backend'), algorithms=['RS256'], issuer=ISSUER, audience=p['audience'])
        self.assertEqual(claims['scope'], p['gateway_scope'])
        class Redirect(issuer_handler(self.root / 'issuer')):
            def do_POST(inner):
                inner.send(302, headers={'Location': self.urls['accounts'] + '/healthz'})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.root / 'issuer' / 'tls.pem'), str(self.root / 'issuer' / 'tls.key'))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            provider.token_endpoint = f'https://localhost:{server.server_port}/token'
            with self.assertRaisesRegex(GatewayError, 'HTTP 302'):
                provider.get_token(p['audience'], (p['gateway_scope'],))
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_credentials_are_separated(self):
        for name in ('gateway', 'backend', 'client'):
            self.assertFalse((self.root / name / 'signing.key').exists())
        self.assertFalse((self.root / 'client' / 'exchange-secret').exists())
        self.assertFalse((self.root / 'gateway' / 'agent-secret').exists())


if __name__ == '__main__': unittest.main()
