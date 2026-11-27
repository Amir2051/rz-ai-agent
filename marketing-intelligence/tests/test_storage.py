import unittest
from unittest.mock import MagicMock
from marketing.storage import save,list_drafts
U='00000000-0000-4000-8000-000000000001'; W='00000000-0000-4000-8000-000000000002'
class Storage(unittest.TestCase):
 def test_save(self):
    c=MagicMock();c.execute.return_value.fetchone.return_value=('id',);self.assertEqual(save(c,U,W,'campaign',{'external_execution':False})['id'],'id');self.assertEqual(c.execute.call_args_list[0].args[0],'SET LOCAL ROLE marketing_drafter');self.assertEqual(c.execute.call_args_list[1].args[1],(U,))
 def test_execution_denied(self):
    c=MagicMock()
    with self.assertRaises(ValueError):save(c,U,W,'campaign',{'external_execution':True})
    c.execute.assert_not_called()
 def test_kind_denied(self):
    with self.assertRaises(ValueError):save(MagicMock(),U,W,'publish',{'external_execution':False})
 def test_read(self):
    c=MagicMock();c.execute.return_value.fetchall.return_value=[];self.assertEqual(list_drafts(c,U,W),[]);self.assertEqual(c.execute.call_args_list[0].args[0],'SET LOCAL ROLE marketing_reader')
