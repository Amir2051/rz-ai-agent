"""Read-only Google data grants, separate from Google identity sign-in."""

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from time import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
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
