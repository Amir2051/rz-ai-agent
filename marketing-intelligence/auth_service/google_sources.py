"""Read-only Google data grants, separate from Google identity sign-in."""

import base64
from datetime import date, timedelta
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from time import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
import httpx
from pydantic import BaseModel, Field
from uuid import UUID

from auth_service import activation, google_login

STATE_PREFIX = 'mi-source-'
STATE_COOKIE = '__Host-mi_google_source'
GOOGLE_SCOPES = {
    'ga4': 'https://www.googleapis.com/auth/analytics.readonly',
    'search-console': 'https://www.googleapis.com/auth/webmasters.readonly',
}
NAMES = {'ga4': 'Google Analytics 4', 'search-console': 'Google Search Console'}
TOKEN_URL = 'https://oauth2.googleapis.com/token'
EXPIRY = 600


def protect(value, label):
    return activation.seal(json.dumps(value, separators=(',', ':')), label.encode())


def reveal(value, label):
    return json.loads(activation.unseal(value, label.encode()))


@contextmanager
def store():
    configured = os.getenv('MI_SOURCE_STORE', '')
    path = Path(configured)
    if not configured or not path.is_absolute():
        raise HTTPException(503, 'Google connections are unavailable')
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
        raise HTTPException(503, 'Google connections are unavailable')
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise HTTPException(503, 'Google connections are unavailable')
        finally:
            os.close(fd)
        conn = sqlite3.connect(path, timeout=5)
        try:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS pending (
                    id TEXT PRIMARY KEY, phase TEXT NOT NULL, session_digest TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, payload BLOB NOT NULL)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS connections (
                    workspace TEXT NOT NULL, source TEXT NOT NULL, property_id TEXT NOT NULL,
                    name TEXT NOT NULL, credential BLOB NOT NULL,
                    PRIMARY KEY (workspace,source,property_id))""")
                yield conn
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        raise HTTPException(503, 'Google connections are unavailable') from None


def _google_config():
    try:
        return google_login.config(os.environ['MI_GOOGLE_CLIENT_JSON'])
    except (ValueError, OSError, KeyError, TypeError):
        raise HTTPException(503, 'Google source OAuth is not configured') from None


def _authorize(client_id, scope, state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': client_id, 'redirect_uri': google_login.CALLBACK,
        'response_type': 'code', 'scope': scope, 'state': state, 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'select_account consent',
    })


def _properties(client, source, token):
    headers = {'Authorization': 'Bearer ' + token}
    found = {}
    if source == 'search-console':
        result = client.get('https://www.googleapis.com/webmasters/v3/sites', headers=headers)
        result.raise_for_status()
        for site in result.json().get('siteEntry', []):
            value = site['siteUrl']
            if site.get('permissionLevel') != 'siteUnverifiedUser':
                found[value] = value
        return found

    page = None
    for _ in range(50):
        params = {'pageSize': 200}
        if page:
            params['pageToken'] = page
        result = client.get('https://analyticsadmin.googleapis.com/v1beta/accountSummaries',
                            params=params, headers=headers)
        result.raise_for_status()
        data = result.json()
        for account in data.get('accountSummaries', []):
            for prop in account.get('propertySummaries', []):
                name = prop['displayName']
                found[prop['property']] = name + ' (' + account['displayName'] + ')'
        page = data.get('nextPageToken')
        if not page:
            return found
    raise ValueError('Google property pagination exceeded the request limit')


def _ga_series(rows, labels, dimension):
    series = []
    for row in rows:
        value = row['dimensionValues'][0]['value']
        if value.startswith('RESERVED_'):
            continue
        if dimension == 'date':
            value = f'{value[:4]}-{value[4:6]}-{value[6:]}'
        series.append({'time': value, **dict(zip(labels, (int(m['value']) for m in row['metricValues'])))})
    return series


def _report(client, source, property_id, refresh, config, *, realtime=False, series=False, days=28):
    response = client.post(TOKEN_URL, data={
        'refresh_token': refresh, 'client_id': config['client_id'],
        'client_secret': config['client_secret'], 'grant_type': 'refresh_token',
    })
    response.raise_for_status()
    token = response.json()['access_token']
    headers = {'Authorization': 'Bearer ' + token}
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    if source == 'ga4':
        if not re.fullmatch(r'properties/[0-9]+', property_id):
            raise ValueError('Invalid GA4 property')
        if realtime:
            response = client.post(
                'https://analyticsdata.googleapis.com/v1beta/' + property_id + ':runRealtimeReport',
                headers=headers, json={
                    'minuteRanges': [{'startMinutesAgo': 29, 'endMinutesAgo': 0}],
                    'metrics': [{'name': 'activeUsers'}, {'name': 'eventCount'}],
                    **({'dimensions': [{'name': 'minutesAgo'}], 'metricAggregations': ['TOTAL']} if series else {}),
                })
            response.raise_for_status()
            data = response.json()
            rows = data.get('rows', [])
            totals = data.get('totals', []) if series else rows
            labels = ('Active users', 'Events')
            metrics = dict(zip(labels, (int(item['value']) for item in totals[0]['metricValues']))) if rows and totals else {}
            result = {'minutes': 30, 'metrics': metrics}
            if series:
                result['series'] = _ga_series(rows, labels, 'minutesAgo')
            return result
        response = client.post(
            'https://analyticsdata.googleapis.com/v1beta/' + property_id + ':runReport',
            headers=headers, json={
                'dateRanges': [{'startDate': str(start), 'endDate': str(end)}],
                'metrics': [{'name': 'sessions'}, {'name': 'screenPageViews'}],
                **({'dimensions': [{'name': 'date'}], 'metricAggregations': ['TOTAL']} if series else {}),
            })
        response.raise_for_status()
        data = response.json()
        rows = data.get('rows', [])
        labels = ('Sessions', 'Views')
        totals = data.get('totals', []) if series else rows
        values = [int(metric['value']) for metric in totals[0]['metricValues']] if rows and totals else []
        points = _ga_series(rows, labels, 'date') if series else []
    else:
        response = client.post(
            'https://www.googleapis.com/webmasters/v3/sites/' + quote(property_id, safe='') + '/searchAnalytics/query',
            headers=headers, json={'startDate': str(start), 'endDate': str(end), 'rowLimit': days,
                                   **({'dimensions': ['date']} if series else {})})
        response.raise_for_status()
        rows = response.json().get('rows', [])
        labels = ('Clicks', 'Impressions')
        values = [sum(row['clicks'] for row in rows), sum(row['impressions'] for row in rows)] if rows else []
        points = [{'time': row['keys'][0], 'Clicks': row['clicks'], 'Impressions': row['impressions']}
                  for row in rows] if series else []
    result = {'start': str(start), 'end': str(end), 'metrics': dict(zip(labels, values))}
    if series:
        result['series'] = points
    return result


def _google_error(error):
    try:
        details = error.response.json().get('error', {})
    except (ValueError, AttributeError):
        details = {}
    if details == 'invalid_grant' or error.response.status_code == 401:
        return 'reconnect'
    if isinstance(details, dict) and (
        any(isinstance(item, dict) and item.get('reason') in ('SERVICE_DISABLED', 'accessNotConfigured')
            for item in details.get('details', []) + details.get('errors', []))
    ):
        return 'api-disabled'
    return 'permission' if error.response.status_code == 403 else 'failed'


def exchange(source, code, verifier, config):
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        response = client.post(TOKEN_URL, data={
            'code': code, 'code_verifier': verifier, 'client_id': config['client_id'],
            'client_secret': config['client_secret'], 'redirect_uri': google_login.CALLBACK,
            'grant_type': 'authorization_code',
        })
        response.raise_for_status()
        response_data = response.json()
        if set(response_data.get('scope', '').split()) != {GOOGLE_SCOPES[source]}:
            raise ValueError('Expected exactly the provider read-only scope')
        refresh = response_data.get('refresh_token')
        if not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192:
            raise ValueError('Missing refresh token')
        properties = _properties(client, source, response_data['access_token'])
        return refresh, properties


class Selection(BaseModel):
    grant: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    properties: list[str] = Field(min_length=1, max_length=200)


def routes(db, current_user, origin, session_cookie):
    router = APIRouter()

    def session(request):
        token = request.cookies.get(session_cookie, '')
        return activation.digest(token)

    def require_manager(workspace, user):
        with db('marketing_reader') as conn:
            conn.execute("SELECT set_config('app.user_id',%s,true)", (user['id'],))
            allowed = conn.execute("""SELECT 1 FROM public.workspaces w
                JOIN public.organization_members m ON m.organization_id=w.organization_id
                WHERE w.id=%s AND m.user_id=%s AND m.role IN ('owner','admin')""",
                                   (workspace, user['id'])).fetchone()
        if not allowed:
            raise HTTPException(404, 'Workspace not available')

    @router.get('/v1/workspaces/{workspace}/google-sources')
    def connected(workspace: UUID, user=Depends(current_user)):
        require_manager(workspace, user)
        with store() as conn:
            rows = conn.execute("""SELECT source,property_id,name FROM connections WHERE workspace=?
                ORDER BY source,name,property_id""", (str(workspace),)).fetchall()
        return [{'source': source, 'id': prop, 'name': name} for source,prop,name in rows]

    @router.get('/v1/workspaces/{workspace}/google-sources/{source}/report')
    def report(workspace: UUID, source: str, property_id: str, realtime: bool = False, series: bool = False,
               days: int = Query(28, ge=1, le=90), user=Depends(current_user)):
        if realtime and source != 'ga4':
            raise HTTPException(422, 'Realtime reports require a GA4 property')
        if source not in GOOGLE_SCOPES or len(property_id) > 2048:
            raise HTTPException(404, 'Property not connected')
        require_manager(workspace, user)
        workspace_id = str(workspace)
        with store() as conn:
            row = conn.execute("""SELECT credential FROM connections
                WHERE workspace=? AND source=? AND property_id=?""",
                               (workspace_id, source, property_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Property not connected')
        label = json.dumps([workspace_id, source, property_id], separators=(',', ':'))
        try:
            refresh = reveal(row[0], label)
            with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
                options = {'series': True, 'days': days} if series or days != 28 else {}
                return _report(client, source, property_id, refresh, _google_config(), realtime=realtime, **options)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, _google_error(exc)) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, 'failed') from None

    @router.post('/v1/workspaces/{workspace}/google-sources/{source}/start')
    def start(workspace: UUID, source: str, request: Request, user=Depends(current_user)):
        origin(request)
        if source not in GOOGLE_SCOPES:
            raise HTTPException(404, 'Source not available')
        require_manager(workspace, user)
        config = _google_config()
        state = STATE_PREFIX + secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        key = activation.digest(state)
        sid = session(request)
        now = int(time())
        record = {'workspace': str(workspace), 'source': source, 'verifier': verifier}
        with store() as conn:
            conn.execute('DELETE FROM pending WHERE expires_at <= ? OR (session_digest=? AND phase=?)',
                         (now,sid,'authorize'))
            conn.execute('INSERT INTO pending VALUES (?,?,?,?,?)',
                         (key,'authorize',sid,now+EXPIRY,protect(record,key)))
        from fastapi.responses import JSONResponse
        response = JSONResponse({'url': _authorize(config['client_id'],GOOGLE_SCOPES[source],state,verifier)})
        response.set_cookie(STATE_COOKIE,state,max_age=EXPIRY,secure=True,httponly=True,samesite='lax',path='/')
        return response

    def finish(request):
        def back(result):
            response = RedirectResponse('/?'+urlencode({'google_source':result})+'#connected',status_code=303)
            response.delete_cookie(STATE_COOKIE,path='/',secure=True,httponly=True,samesite='lax')
            return response

        state = request.query_params.get('state', '')
        cookie = request.cookies.get(STATE_COOKIE, '')
        if len(state)!=len(STATE_PREFIX)+43 or not hmac.compare_digest(state.encode(),cookie.encode()):
            return back('expired')
        try:
            user = current_user(request)
            key = activation.digest(state)
            sid = session(request)
            with store() as conn:
                row = conn.execute("""DELETE FROM pending WHERE id=? AND phase='authorize'
                    AND session_digest=? AND expires_at>? RETURNING payload""", (key,sid,int(time()))).fetchone()
            if not row:
                return back('expired')
            record = reveal(row[0],key)
            require_manager(UUID(record['workspace']),user)
            code = request.query_params.get('code', '')
            if not 1 <= len(code) <= 2048 or request.query_params.get('error'):
                return back('denied')
            refresh, properties = exchange(record['source'],code,record['verifier'],_google_config())
            if not properties:
                return back('empty')
            grant = secrets.token_urlsafe(32)
            payload = {k: record[k] for k in ('workspace','source')}
            payload.update(refresh=refresh,properties=properties)
            with store() as conn:
                conn.execute('INSERT INTO pending VALUES (?,?,?,?,?)',
                    (activation.digest(grant),'select',sid,int(time())+EXPIRY,
                     protect(payload,activation.digest(grant))))
        except httpx.HTTPStatusError as exc:
            return back(_google_error(exc))
        except (HTTPException,httpx.HTTPError,ValueError,KeyError,TypeError):
            return back('failed')
        # The grant is session-bound. A fragment keeps it out of HTTP requests.
        response = RedirectResponse('/#connected/'+grant,status_code=303)
        response.delete_cookie(STATE_COOKIE,path='/',secure=True,httponly=True,samesite='lax')
        return response

    @router.get('/v1/google-source-selection/{grant}')
    def selection(grant: str, request: Request, user=Depends(current_user)):
        key = activation.digest(grant)
        with store() as conn:
            row = conn.execute("""SELECT payload FROM pending
                WHERE id=? AND phase='select' AND session_digest=? AND expires_at>?""",
                               (key,session(request),int(time()))).fetchone()
        if not row:
            raise HTTPException(404,'Google selection expired')
        record = reveal(row[0],key)
        require_manager(UUID(record['workspace']),user)
        return {'workspace':record['workspace'],'source':NAMES[record['source']],
                'properties':[{'id':prop,'name':name} for prop,name in record['properties'].items()]}

    @router.post('/v1/google-source-selection')
    def save(payload: Selection, request: Request, user=Depends(current_user)):
        origin(request)
        key = activation.digest(payload.grant)
        sid = session(request)
        with store() as conn:
            row = conn.execute("""SELECT payload FROM pending
                WHERE id=? AND phase='select' AND session_digest=? AND expires_at>?""",
                               (key,sid,int(time()))).fetchone()
        if not row:
            raise HTTPException(404,'Google selection expired')
        record = reveal(row[0],key)
        workspace,source = record['workspace'],record['source']
        require_manager(UUID(workspace),user)
        selected=set(payload.properties)
        if not selected.issubset(record['properties']):
            raise HTTPException(422,'Select only properties returned by Google')
        with store() as conn:
            claimed = conn.execute("""DELETE FROM pending WHERE id=? AND phase='select'
                AND session_digest=? AND expires_at>? RETURNING id""",(key,sid,int(time()))).fetchone()
            if not claimed:
                raise HTTPException(404,'Google selection expired')
            for prop in selected:
                label=json.dumps([workspace,source,prop],separators=(',',':'))
                conn.execute("""INSERT INTO connections VALUES (?,?,?,?,?)
                    ON CONFLICT(workspace,source,property_id) DO UPDATE
                    SET name=excluded.name,credential=excluded.credential""",
                    (workspace,source,prop,record['properties'][prop],protect(record['refresh'],label)))
        return {'connected':len(selected)}

    return router,finish
