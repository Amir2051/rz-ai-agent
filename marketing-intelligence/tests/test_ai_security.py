import unittest
from decimal import Decimal
from unittest.mock import Mock
from cryptography.exceptions import InvalidTag
from marketing.ai_security import seal,unseal,ModelPolicy,plan_request,transport
class Security(unittest.TestCase):
 def test_encryption(self):
    k=b'x'*32;b=seal(k,'a','openai','fixture');self.assertNotIn(b'fixture',b);self.assertEqual(unseal(k,'a','openai',b),'fixture')
    with self.assertRaises(InvalidTag):unseal(k,'b','openai',b)
 def policy(self,verified=True):return ModelPolicy('openrouter','fixture',128,2000,Decimal('1'),Decimal('2'),verified)
 def test_zdr(self):
    r,_=plan_request('a',{'a'},self.policy(),'fixture',1);self.assertTrue(r['body']['provider']['zdr']);self.assertFalse(r['body']['provider']['allow_fallbacks'])
 def test_budget(self):
    with self.assertRaises(PermissionError):plan_request('a',{'a'},self.policy(),'fixture',0)
 def test_privacy(self):
    with self.assertRaises(PermissionError):plan_request('a',{'a'},self.policy(False),'fixture',1)
 def test_transport(self):
    r,_=plan_request('a',{'a'},self.policy(),'fixture',1);c=Mock()
    with self.assertRaises(PermissionError):transport(r,'fixture',c)
    c.post.assert_not_called();c.post.return_value.status_code=200;c.post.return_value.json.return_value={'choices':[{'message':{'content':'draft'}}]};self.assertEqual(transport(r,'fixture',c,enabled=True),'draft');self.assertFalse(c.post.call_args.kwargs['follow_redirects'])
