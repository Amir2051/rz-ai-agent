import base64
from contextlib import contextmanager
import json
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx
import pytest

from auth_service import google_sources, hubspot_sources as hubspot

WORKSPACE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
ORIGIN = 'https://marketing.safenestt.com'
COOKIE = '__Host-mi_session'
PATH = f'/v1/workspaces/{WORKSPACE}/hubspot'
CALLBACK = '/oauth/hubspot/callback'


@pytest.fixture
def clients(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setenv('MI_SOURCE_STORE', str(tmp_path / 'sources.sqlite3'))
    monkeypatch.setenv('MI_TOKEN_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'z' * 32).decode())
    config = tmp_path / 'hubspot.json'
    config.write_text(json.dumps({'client_id':'test-client', 'client_secret':'test-secret'}))
    config.chmod(0o600)
    monkeypatch.setenv('MI_HUBSPOT_CLIENT_JSON', str(config))

    class DB:
        def execute(self, sql, args):
            class Result:
                def fetchone(self):
                    return (1,) if str(args[0]) == WORKSPACE and args[1] == 'owner' else None
            return Result()

    @contextmanager
    def db(role):
        assert role == 'marketing_reader'
        yield DB()

    def current_user(request: Request):
        if request.cookies.get(COOKIE) not in {'owner-a', 'owner-b'}:
            raise HTTPException(401)
        return {'id': 'owner'}

    def origin(request):
        if request.headers.get('origin') != ORIGIN:
            raise HTTPException(403)

    app = FastAPI()
    router, _ = google_sources.routes(db, current_user, origin, COOKIE)
    app.include_router(router)
    client = TestClient(app, base_url=ORIGIN)
    other = TestClient(app, base_url=ORIGIN)
    other.cookies.set(COOKIE, 'owner-b')
    return client, other, config


def begin(client):
    response = client.post(PATH+'/start', headers={'Origin':ORIGIN})
    assert response.status_code == 200
    url = urlsplit(response.json()['url'])
    query = parse_qs(url.query)
    assert url.hostname == 'app.hubspot.com'
    assert set(query['scope'][0].split()) == set(hubspot.SCOPES)
    assert query['redirect_uri'] == [hubspot.CALLBACK]
    assert 'test-secret' not in response.text
    assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
    return query['state'][0]


def callback(client, state):
    return client.get(CALLBACK, params={'code':'test-code', 'state':state}, follow_redirects=False)


def test_hubspot_read_only_connect_and_read(clients, monkeypatch):
    client, other, config = clients
    assert client.get(PATH).status_code == 401
    client.cookies.set(COOKIE, 'owner-a')
    assert client.post(PATH+'/start').status_code == 403
    assert client.get(PATH.replace(WORKSPACE, OTHER)).status_code == 404
    config.chmod(0o644)
    assert not client.get(PATH).json()['configured']
    config.chmod(0o600)

    steps = []

    def provider(request):
        steps.append(request.url.path)
        if request.url == hubspot.TOKEN:
            values = parse_qs(request.content.decode())
            assert values['client_secret'] == ['test-secret']
            if values['grant_type'] == ['authorization_code']:
                assert values['code'] == ['test-code']
                return httpx.Response(200, json={'access_token':'mock-access', 'refresh_token':'mock-refresh'})
            assert values['refresh_token'] == ['mock-refresh']
            return httpx.Response(200, json={'access_token':'mock-access', 'hub_id':hubspot.PORTAL,
                                            'scopes':list(hubspot.SCOPES)})
        if request.url == hubspot.TOKEN+'/introspect':
            assert parse_qs(request.content.decode())['token'] == ['mock-access']
            return httpx.Response(200, json={'active':True, 'hub_id':hubspot.PORTAL, 'scopes':list(hubspot.SCOPES)})
        assert request.method == 'GET' and request.headers['authorization'] == 'Bearer mock-access'
        assert request.url.params['limit'] == '10'
        if request.url.path.endswith('/contacts'):
            return httpx.Response(200, json={'results':[{'properties': {
                'firstname':'Sample', 'lastname':'Contact', 'email':'private@example.invalid',
            }}]})
        assert request.url.path.endswith('/deals')
        return httpx.Response(200, json={'results':[{'properties':{'dealname':'Sample deal'}}],
                                        'paging':{'next':{'after':'2'}}})

    monkeypatch.setattr(hubspot, 'provider', lambda: httpx.Client(transport=httpx.MockTransport(provider)))
    state = begin(client)
    other.cookies.set(hubspot.COOKIE, state)
    assert 'hubspot=expired' in callback(other, state).headers['location']
    assert not steps
    assert 'hubspot=connected' in callback(client, state).headers['location']
    client.cookies.set(hubspot.COOKIE, state)
    assert 'hubspot=expired' in callback(client, state).headers['location']
    assert client.get(PATH).json() == {'configured':True, 'portal_id':hubspot.PORTAL}
    assert client.get(PATH.replace('/hubspot','/google-sources')).json() == []
    response = client.get(PATH+'/summary')
    assert response.json() == {
        'contacts': {'rows':[{'firstname':'Sample', 'lastname':'Contact'}], 'has_more':False},
        'deals': {'rows':[{'dealname':'Sample deal'}], 'has_more':True},
    }
    assert response.headers['cache-control'] == 'no-store'
    assert steps.count('/oauth/2026-03/token') == 2
    assert b'mock-refresh' not in (config.parent/'sources.sqlite3').read_bytes()


@pytest.mark.parametrize(('portal','extra_scope'), [('12345', None), (hubspot.PORTAL, 'crm.objects.contacts.write')])
def test_hubspot_rejects_wrong_account_or_scope(clients, monkeypatch, portal, extra_scope):
    client, _, _ = clients
    client.cookies.set(COOKIE, 'owner-a')
    scopes = list(hubspot.SCOPES)
    if extra_scope:
        scopes.append(extra_scope)

    def provider(request):
        return httpx.Response(200, json={'access_token':'mock-access', 'refresh_token':'mock-refresh',
                                        'hub_id':portal, 'scopes':scopes})

    monkeypatch.setattr(hubspot, 'provider', lambda: httpx.Client(transport=httpx.MockTransport(provider)))
    assert 'hubspot=failed' in callback(client, begin(client)).headers['location']
    assert client.get(PATH).json()['portal_id'] is None
    assert client.get(PATH+'/summary').status_code == 404
