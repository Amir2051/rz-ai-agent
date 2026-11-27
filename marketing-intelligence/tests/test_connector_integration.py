import unittest
from datetime import date
from marketing.connectors import Account,normalize,catalog
from hub.connectors.meta_ads import parse_meta_insights
class ConnectorTests(unittest.TestCase):
 def setUp(self):
    self.a=Account('a','meta_ads','act_123');self.allowed=frozenset({self.a});self.d=date(2026,11,1);self.row={'account_id':'act_123','date':'2026-11-01','impressions':12}
 def run_rows(self,rows):return normalize(self.a,self.allowed,rows,self.d,self.d)
 def test_catalog(self):self.assertEqual(len(catalog()),5);self.assertTrue(all(not x['live_enabled'] for x in catalog().values()))
 def test_fixture(self):self.assertEqual(self.run_rows([self.row])['rows'][0]['impressions'],12)
 def test_missing(self):self.assertIsNone(self.run_rows([self.row])['rows'][0]['spend'])
 def test_cross_account(self):
    with self.assertRaises(PermissionError):self.run_rows([{**self.row,'account_id':'act_456'}])
 def test_cross_workspace(self):
    with self.assertRaises(PermissionError):normalize(Account('b','meta_ads','act_123'),self.allowed,[self.row],self.d,self.d)
 def test_wrong_source(self):
    with self.assertRaises(PermissionError):self.run_rows([{**self.row,'source':'ga4'}])
 def test_wrong_report(self):
    with self.assertRaises(PermissionError):self.run_rows([{**self.row,'report':'queries'}])
 def test_bad_metric(self):
    with self.assertRaises(ValueError):self.run_rows([{**self.row,'spend':float('nan')}])
 def test_date(self):
    with self.assertRaises(ValueError):self.run_rows([{**self.row,'date':'2026-11-02'}])
