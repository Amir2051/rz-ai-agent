import base64
import json

import httpx
from urllib.parse import parse_qs, urlsplit

from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from auth_service import google_sources

WORKSPACE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
ORIGIN = 'https://marketing.safenestt.com'
HEADERS = {'Origin': ORIGIN}
COOKIE = '__Host-mi_session'
PROPERTIES = {'properties/100': 'Marketing Analytics', 'properties/200': 'Second property'}


def test_native_google_scopes_replay_tenant_and_selection(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setenv('MI_SOURCE_STORE', str(tmp_path / 'auth.sqlite3'))
    monkeypatch.setenv('MI_TOKEN_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'x' * 32).decode())
    key = tmp_path / 'oauth.json'
    key.write_text(json.dumps({'web':{'client_id':'fixture-client', 'client_secret':'fixture-only',
                                     'redirect_uris':[google_sources.google_login.CALLBACK]}}))
    key.chmod(0o600)
    monkeypatch.setenv('MI_GOOGLE_CLIENT_JSON', str(key))
    monkeypatch.setenv('MI_REGISTRATION_ENABLED', '0')
    owner_sessions = {'session-one':'owner', 'session-two':'owner', 'session-other':'other'}

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
        user = owner_sessions.get(request.cookies.get(COOKIE))
        if not user:
            raise HTTPException(401)
        return {'id':user}

    def origin(request):
        if request.headers.get('origin') != ORIGIN:
            raise HTTPException(403)

    exchanges = []

    def exchange(source,code,verifier,config):
        assert source == 'ga4' and code == 'simulated-code'
        assert 43 <= len(verifier) <= 128 and config['client_id'] == 'fixture-client'
        exchanges.append(code)
        return 'synthetic-refresh-token', PROPERTIES

    monkeypatch.setattr(google_sources, 'exchange', exchange)
    app = FastAPI()
    router, finish = google_sources.routes(db, current_user, origin, COOKIE)
    app.include_router(router)

    @app.get('/callback-test')
    def callback(request: Request):
        return finish(request)

    client = TestClient(app, base_url=ORIGIN)
    other = TestClient(app, base_url=ORIGIN)
    resource = f'/v1/workspaces/{WORKSPACE}/google-sources'
    assert client.get(resource).status_code == 401
    client.cookies.set(COOKIE, 'session-one')
    other.cookies.set(COOKIE, 'session-two')
    assert client.get(f'/v1/workspaces/{OTHER}/google-sources').status_code == 404
    assert client.post(resource+'/ga4/start').status_code == 403
    started = client.post(resource+'/ga4/start',headers=HEADERS)
    assert started.status_code == 200
    url = urlsplit(started.json()['url'])
    query = parse_qs(url.query)
    assert url.hostname == 'accounts.google.com'
    assert query['scope'] == [google_sources.GOOGLE_SCOPES['ga4']]
    assert query['code_challenge_method'] == ['S256']
    state = query['state'][0]
    other.cookies.set(google_sources.STATE_COOKIE, state)
    redirect = other.get('/callback-test',params={'state':state,'code':'simulated-code'},follow_redirects=False)
    assert 'google_source=expired' in redirect.headers['location'] and not exchanges
    redirect = client.get('/callback-test',params={'state':state,'code':'simulated-code'},follow_redirects=False)
    assert redirect.status_code == 303
    grant = redirect.headers['location'].split('/#connected/')[1]
    client.cookies.set(google_sources.STATE_COOKIE, state)
    replay = client.get('/callback-test',params={'state':state,'code':'simulated-code'},follow_redirects=False)
    assert 'google_source=expired' in replay.headers['location']
    assert len(exchanges) == 1
    selection = '/v1/google-source-selection'
    assert other.get(selection+'/'+grant).status_code == 404
    assert len(client.get(selection+'/'+grant).json()['properties']) == 2
    assert client.post(selection,headers=HEADERS,json={'grant':grant,'properties':['forged-account']}).status_code == 422
    saved = client.post(selection,headers=HEADERS,json={'grant':grant,'properties':['properties/200']})
    assert saved.json() == {'connected':1}
    assert client.post(selection,headers=HEADERS,json={'grant':grant,'properties':['properties/200']}).status_code == 404
    assert client.get(resource).json() == [{'source':'ga4','id':'properties/200','name':'Second property'}]
    assert b'synthetic-refresh-token' not in (tmp_path/'auth.sqlite3').read_bytes()

    def report(_client, source, prop, refresh, config):
        assert (source, prop, refresh, config['client_id']) == (
            'ga4', 'properties/200', 'synthetic-refresh-token', 'fixture-client')
        return {'metrics': {'Sessions': 4}}
    monkeypatch.setattr(google_sources, '_report', report)
    assert client.get(resource+'/ga4/report', params={'property_id':'properties/100'}).status_code == 404
    assert client.get(resource+'/ga4/report', params={'property_id':'properties/200'}).json() == {'metrics': {'Sessions': 4}}


def test_google_reports_use_provider_api_and_refresh_token():
    def respond(request):
        if request.url.host == 'oauth2.googleapis.com':
            assert parse_qs(request.content.decode())['grant_type'] == ['refresh_token']
            return httpx.Response(200, json={'access_token':'fixture'})
        assert request.headers['authorization'] == 'Bearer fixture'
        if request.url.host == 'analyticsdata.googleapis.com':
            assert request.url.path == '/v1beta/properties/200:runReport'
            return httpx.Response(200, json={'rows':[{'metricValues':[{'value':'5'},{'value':'9'}]}]})
        assert request.url.host == 'www.googleapis.com'
        assert 'sc-domain%3Aexample.invalid' in str(request.url)
        assert request.url.path.endswith('/searchAnalytics/query')
        return httpx.Response(200, json={'rows':[{'clicks':2,'impressions':8}]})

    config = {'client_id':'fixture-client','client_secret':'fixture-only'}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        ga = google_sources._report(client,'ga4','properties/200','fixture',config)
        gsc = google_sources._report(client,'search-console','sc-domain:example.invalid','fixture',config)
        assert ga['metrics'] == {'Sessions':5,'Views':9}
        assert gsc['metrics'] == {'Clicks':2,'Impressions':8}
