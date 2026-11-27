import unittest
from uuid import UUID
from fastapi.testclient import TestClient
from auth_service.app import app
from auth_service.drafts import DraftInput,build
from fastapi import HTTPException
W=UUID('00000000-0000-4000-8000-000000000002')
class DraftAPI(unittest.TestCase):
 def test_unauth_read(self):self.assertEqual(TestClient(app).get(f'/v1/workspaces/{W}/drafts').status_code,401)
 def test_unauth_write(self):self.assertEqual(TestClient(app).post(f'/v1/workspaces/{W}/drafts',json={'kind':'lead','title':'fixture'}).status_code,401)
 def test_lead(self):self.assertFalse(build(W,DraftInput(kind='lead',title='fixture'))['external_execution'])
 def test_campaign(self):self.assertEqual(build(W,DraftInput(kind='campaign',title='fixture',start='2026-12-01',end='2026-12-02'))['budget_cents'],0)
 def test_invalid(self):
    with self.assertRaises(HTTPException):build(W,DraftInput(kind='campaign',title='fixture'))
 def test_extra(self):
    with self.assertRaises(ValueError):DraftInput(kind='lead',title='fixture',actor_id='other')
