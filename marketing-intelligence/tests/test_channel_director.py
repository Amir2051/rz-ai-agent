from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from auth_service import channel_director

WORKSPACE = 'a1b2c3d4-1234-4321-8765-abcd12345678'
PATH = f'/v1/workspaces/{WORKSPACE}'
SITE = {'source':'search-console', 'name':'SafeNestT', 'id':'sc-domain:example.invalid'}
GA = {'source':'ga4', 'name':'SafeNestT', 'id':'properties/123'}
METRICS = {'start':'2026-11-20', 'end':'2026-11-26', 'metrics':{'Clicks':0, 'Impressions':16}}


def test_channel_director_uses_provider_data_and_checks_permission(monkeypatch):
    def current_user(request: Request):
        if request.headers.get('x-test-user') != 'owner':
            raise HTTPException(401)
        return {'id':'owner'}

    def origin(request):
        if request.headers.get('origin') != 'https://example.invalid':
            raise HTTPException(403)

    def connected(workspace, user):
        if workspace != UUID(WORKSPACE) or user['id'] != 'owner':
            raise HTTPException(404)
        return [SITE, GA]

    def report(workspace, source, property_id, *, days, user):
        assert workspace == UUID(WORKSPACE)
        assert days == 7
        assert user['id'] == 'owner'
        if source == 'ga4':
            raise HTTPException(502, 'reconnect')
        assert property_id == SITE['id']
        return METRICS

    calls = []

    def generate(objective, reports, missing):
        assert reports == [{**SITE, **METRICS, 'status':'available'}]
        assert missing == ['ga4', 'youtube']
        calls.append(objective)
        return {'brief':'Fixture brief', 'next_step':'Fixture next step'}

    monkeypatch.setattr(channel_director, 'generate', generate)
    router = APIRouter()
    channel_director.register(router, current_user, origin, connected, report)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    headers = {'x-test-user':'owner', 'origin':'https://example.invalid'}
    assert client.get(PATH+'/channel-overview').status_code == 401
    assert client.post(PATH+'/channel-brief', json={}, headers={'x-test-user':'owner'}).status_code == 403
    overview = client.get(PATH+'/channel-overview', headers=headers).json()
    assert overview['reports'][0]['metrics']['Clicks'] == 0
    assert overview['reports'][1]['detail'] == 'reconnect'
    assert client.post(PATH+'/channel-brief', json={'objective':'wrong'}, headers=headers).status_code == 422
    assert client.post(PATH+'/channel-brief', json={'objective':'summary','metrics':{}}, headers=headers).status_code == 422
    out = client.post(PATH+'/channel-brief', json={'objective':'summary'}, headers=headers)
    assert out.status_code == 200
    assert out.json()['evidence'] == [{**SITE, **METRICS, 'status':'available'}]
    assert len(calls) == 1
