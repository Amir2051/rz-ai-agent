import unittest
from gateway.policy import Scope, execute

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.scope=Scope('demo','account1')
        self.allowed=frozenset({self.scope})
    def test_read(self):
        self.assertTrue(execute('synthetic.ga4.summary',self.scope,self.allowed)['synthetic'])
    def test_unknown(self):
        with self.assertRaises(PermissionError): execute('unknown',self.scope,self.allowed)
    def test_write(self):
        with self.assertRaises(PermissionError): execute('meta_ads_create_campaign',self.scope,self.allowed)
    def test_cross_tenant(self):
        with self.assertRaises(PermissionError): execute('synthetic.ga4.summary',Scope('other','account1'),self.allowed)
    def test_cross_account(self):
        with self.assertRaises(PermissionError): execute('synthetic.ga4.summary',Scope('demo','account2'),self.allowed)
    def test_empty_permissions(self):
        with self.assertRaises(PermissionError): execute('synthetic.ga4.summary',self.scope,frozenset())
