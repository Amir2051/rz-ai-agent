import unittest
from datetime import datetime,timezone
from marketing.workflows import Lead,lead_preview,director,automation,authorize_action
class Workflows(unittest.TestCase):
 def test_lead(self):self.assertFalse(lead_preview(Lead('w','fixture',True),{'w'})['outreach_enabled'])
 def test_lead_scope(self):
    with self.assertRaises(PermissionError):lead_preview(Lead('x','fixture',True),{'w'})
 def test_director(self):self.assertFalse(director('w','leads',{'w'})['model_called'])
 def test_director_scope(self):
    with self.assertRaises(PermissionError):director('x','leads',{'w'})
 def test_automation(self):self.assertFalse(automation('w','report',datetime.now(timezone.utc),{'w'})['scheduled'])
 def test_publish_denied(self):
    with self.assertRaises(PermissionError):authorize_action('w','publish',0,{'w'})
 def test_spend_denied(self):
    with self.assertRaises(PermissionError):authorize_action('w','draft',1,{'w'})
 def test_preview(self):self.assertTrue(authorize_action('w','preview',0,{'w'}))
