import unittest
from datetime import datetime,timezone
from marketing.reporting import summarize
from marketing.content import Draft,preview
class Reports(unittest.TestCase):
 def setUp(self):
    self.r={'source':'gsc','account_id':'a','report':'core','date':'2026-11-01','impressions':10,'clicks':2};self.b={'workspace':'w','source':'gsc','account_id':'a','mode':'offline_fixture','live_verified':False,'rows':[self.r]}
 def report(self):return summarize(self.b,'w','gsc','a')
 def test_ctr(self):self.assertEqual(self.report()['ctr_percent'],20)
 def test_missing(self):self.assertIsNone(self.report()['metrics']['sessions']);self.assertIsNone(self.report()['spend'])
 def test_empty(self):self.b['rows']=[];self.assertIsNone(self.report()['metrics']['clicks'])
 def test_zero(self):self.r['impressions']=0;self.assertIsNone(self.report()['ctr_percent'])
 def test_duplicate(self):
    self.b['rows']*=2
    with self.assertRaises(ValueError):self.report()
 def test_cross_account(self):
    self.r['account_id']='b'
    with self.assertRaises(PermissionError):self.report()
 def test_partial(self):self.b['rows'].append({**self.r,'date':'2026-11-02','clicks':None});self.assertIsNone(self.report()['metrics']['clicks'])
 def test_live_denied(self):
    self.b['live_verified']=True
    with self.assertRaises(ValueError):self.report()
class Content(unittest.TestCase):
 def test_draft(self):self.assertFalse(preview(Draft('w','facebook','Example',datetime.now(timezone.utc)),frozenset({'w'}))['scheduled'])
 def test_cross_tenant(self):
    with self.assertRaises(PermissionError):preview(Draft('w','facebook','Example',datetime.now(timezone.utc)),frozenset({'x'}))
 def test_timezone(self):
    with self.assertRaises(ValueError):preview(Draft('w','facebook','Example',datetime.now()),frozenset({'w'}))
