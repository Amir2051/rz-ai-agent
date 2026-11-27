import unittest,os
from unittest.mock import patch
from fastapi.testclient import TestClient
from auth_service.app import app,digest
class AuthPreview(unittest.TestCase):
 def setUp(self):
    self.env=patch.dict(os.environ,{'MI_APP_ORIGIN':'https://marketing.safenestt.com','MI_LOGIN_ENABLED':'0','MI_DATABASE_URL':''});self.env.start();self.addCleanup(self.env.stop);self.c=TestClient(app,base_url='https://marketing.safenestt.com')
 def test_home_login(self): self.assertIn('Welcome back',self.c.get('/').text)
 def test_no_session(self): self.assertEqual(self.c.get('/v1/auth/me').status_code,401)
 def test_workspace_requires_session(self): self.assertEqual(self.c.get('/v1/workspaces').status_code,401)
 def test_registration_disabled(self): self.assertEqual(self.c.post('/v1/auth/register',json={}).status_code,404)
 def test_origin(self): self.assertEqual(self.c.post('/v1/auth/login',json={'email':'a@example.invalid','password':'synthetic-test-password'}).status_code,403)
 def test_login_disabled(self): self.assertEqual(self.c.post('/v1/auth/login',headers={'origin':'https://marketing.safenestt.com'},json={'email':'a@example.invalid','password':'synthetic-test-password'}).status_code,503)
 def test_no_store(self): self.assertEqual(self.c.get('/').headers['cache-control'],'no-store')
 def test_hash(self): self.assertEqual(len(digest('synthetic')),64)
