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


def test_automation_notes_do_not_schedule_execution():
    draft = build(W, DraftInput(
        kind='automation', title='Review channel totals', task='review',
        planned_at='2026-12-04T09:00:00-05:00', body='Review notes',
    ))
    assert draft['notes'] == 'Review notes'
    assert draft['kind'] == 'review'
    assert draft['scheduled'] is False
    assert draft['external_execution'] is False
