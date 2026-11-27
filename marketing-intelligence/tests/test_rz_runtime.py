import unittest
from marketing.rz_runtime import tool_allowed
class Runtime(unittest.TestCase):
 def test_preview(self):self.assertTrue(tool_allowed('a',{'a'},'marketing.preview'))
 def test_write_denied(self):
    with self.assertRaises(PermissionError):tool_allowed('a',{'a'},'marketing.publish')
 def test_tenant_denied(self):
    with self.assertRaises(PermissionError):tool_allowed('b',{'a'},'marketing.report')
