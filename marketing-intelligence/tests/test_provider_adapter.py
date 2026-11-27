import unittest
from datetime import date
from marketing.provider_adapter import adapt_fixture
from marketing.connectors import Account
D=date(2026,11,1)
FIXTURES={
'ga4':{'dimensionHeaders':[{'name':'date'}],'metricHeaders':[{'name':'sessions'}],'rows':[{'dimensionValues':[{'value':'20261101'}],'metricValues':[{'value':'12'}]}]},
'gsc':{'rows':[{'keys':['2026-11-01'],'clicks':2,'impressions':12}]},
'meta_ads':[{'date_start':'2026-11-01','impressions':'12','clicks':'2'}],
'google_ads':[{'segments.date':'2026-11-01','campaign.id':1,'campaign.name':'fixture','metrics.impressions':12,'metrics.clicks':2,'metrics.cost_micros':1000000,'metrics.conversions':0,'metrics.conversions_value':0}],
'youtube':{'columnHeaders':[{'name':'day'},{'name':'views'}],'rows':[['2026-11-01',12]]}}
class Providers(unittest.TestCase):
 def test_all(self):
    for source,response in FIXTURES.items():
      with self.subTest(source=source):
        a=Account('fixture',source,'fixture-account');r=adapt_fixture(a,frozenset({a}),response,D,D);self.assertEqual(len(r['rows']),1);self.assertFalse(r['live_verified']);self.assertEqual(r['rows'][0]['account_id'],a.account_id)
 def test_denied(self):
    with self.assertRaises(PermissionError):adapt_fixture(Account('other','ga4','x'),frozenset(),{},D,D)
 def test_missing_meta_actions(self):
    a=Account('fixture','meta_ads','x');self.assertIsNone(adapt_fixture(a,frozenset({a}),FIXTURES['meta_ads'],D,D)['rows'][0]['conversions'])
