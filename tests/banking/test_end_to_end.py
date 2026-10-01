"""Run inside the orchestrator container against the real Compose gateway."""
import os
import unittest

from foss_agentic_gateway import GatewayClient, GatewayError
from banking_demo.config import AGENT_ID, EXCHANGE_ID, GATEWAY, POLICIES


@unittest.skipUnless(os.environ.get('BANKING_INTEGRATION') == '1', 'requires the Compose banking topology')
class GatewayIntegration(unittest.TestCase):
    def setUp(self):
        from orchestrator import BankingOrchestrator
        self.agent = BankingOrchestrator()

    def check_receipt(self, name, receipt):
        # A diagnostic assertion in tests, never an SDK authorization decision.
        policy = POLICIES[name]
        self.assertEqual(receipt['audience'], policy['resource'])
        self.assertEqual(receipt['scope'], policy['backend_scope'])
        self.assertEqual(receipt['subject'], AGENT_ID)
        self.assertEqual(receipt['actor'], EXCHANGE_ID)

    def test_agent_to_agent_and_multiple_mcp_servers(self):
        review = self.agent.review_transaction()
        self.check_receipt('review', review['message']['metadata']['verified_upstream_identity'])
        data = self.agent.account_overview(['accounts', 'transactions'])
        for name in data: self.check_receipt(name, data[name]['verified_upstream_identity'])
        self.assertTrue(data['accounts']['synthetic'])
        self.assertEqual(len(data['transactions']['transactions']), 3)

    def test_wrong_gateway_audience_is_rejected(self):
        policy = POLICIES['accounts']
        token = self.agent.gateway.token_provider.get_token(policy['audience'], (policy['gateway_scope'],))
        class WrongTokenProvider:
            def get_token(self, *args): return token
        client = GatewayClient(gateway_url=GATEWAY, token_provider=WrongTokenProvider(), ca_file='/credentials/ca.pem')
        with self.assertRaisesRegex(GatewayError, 'HTTP 401'):
            client.call_tool(self.agent.endpoint('transactions'), 'list_recent_transactions', {'account_id': 'DEMO-001'})


if __name__ == '__main__': unittest.main()
