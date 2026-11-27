import unittest
from datetime import date
from marketing.planning import Campaign,plan
class PlanningTests(unittest.TestCase):
 def campaign(self,**kw):
    args=dict(workspace='a',title='Education',channel='facebook',objective='awareness',start=date(2026,12,1),end=date(2026,12,7));args.update(kw);return Campaign(**args)
 def test_draft(self):
    p=plan(self.campaign(),frozenset({'a'}));self.assertFalse(p['external_execution']);self.assertIsNone(p['metrics']);self.assertTrue(p['requires_review'])
 def test_cross_workspace(self):
    with self.assertRaises(PermissionError):plan(self.campaign(),frozenset({'b'}))
 def test_paid_denied(self):
    with self.assertRaises(PermissionError):self.campaign(budget_cents=1)
 def test_invalid_dates(self):
    with self.assertRaises(ValueError):self.campaign(end=date(2026,11,1))
 def test_unknown_channel(self):
    with self.assertRaises(ValueError):self.campaign(channel='unknown')
 def test_unknown_objective(self):
    with self.assertRaises(ValueError):self.campaign(objective='unknown')
