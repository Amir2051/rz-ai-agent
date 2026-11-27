import unittest
from marketing.ai_providers import prepare,extract_text,PROVIDERS
class Providers(unittest.TestCase):
 def test_all(self):
    for p in PROVIDERS:
      with self.subTest(provider=p):
        r=prepare(p,'fixture-model','synthetic prompt');self.assertFalse(r['execution_enabled']);self.assertEqual(r['body']['max_tokens'],512)
 def test_custom_denied(self):
    with self.assertRaises(PermissionError):prepare('custom','m','p',custom_endpoint='https://example.invalid/v1/chat/completions')
 def test_custom_approved(self):
    u='https://example.invalid/v1/chat/completions';self.assertEqual(prepare('custom','m','p',custom_endpoint=u,approved_endpoints={u})['endpoint'],u)
 def test_override_denied(self):
    with self.assertRaises(ValueError):prepare('openai','m','p',custom_endpoint='https://example.invalid')
 def test_limit(self):
    with self.assertRaises(ValueError):prepare('openai','m','p',max_tokens=2049)
 def test_anthropic(self):self.assertEqual(extract_text('anthropic',{'content':[{'type':'text','text':'fixture'}]}),'fixture')
 def test_openai(self):self.assertEqual(extract_text('openai',{'choices':[{'message':{'content':'fixture'}}]}),'fixture')
