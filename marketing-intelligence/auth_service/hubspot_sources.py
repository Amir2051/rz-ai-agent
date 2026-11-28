"""Workspace-scoped, read-only HubSpot OAuth and CRM reads."""

import hmac
import json
import os
from pathlib import Path
import secrets
import stat
from time import time
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
import httpx

from auth_service import activation

PORTAL = '247360630'
SOURCE = 'hubspot'
PHASE = 'hubspot-authorize'
COOKIE = '__Host-mi_hubspot_source'
EXPIRY = 600
SCOPES = ('oauth', 'crm.objects.contacts.read', 'crm.objects.deals.read')
CALLBACK = 'https://marketing.safenestt.com/oauth/hubspot/callback'
API = 'https://api.hubapi.com'
TOKEN = API + '/oauth/2026-03/token'
RESOURCE = '/v1/workspaces/{workspace}/hubspot'


def config():
    try:
        path = Path(os.environ['MI_HUBSPOT_CLIENT_JSON'])
        if not path.is_absolute():
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 8192):
                raise ValueError
            data = json.load(stream)
        return {key: _token_field(data, key) for key in ('client_id', 'client_secret')}
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'HubSpot OAuth is not configured') from None


def _token_field(data, key):
    value = data[key]
    if not isinstance(value, str) or not 1 <= len(value) <= 8192:
        raise ValueError('Invalid HubSpot credential')
    return value


def provider():
    return httpx.Client(timeout=12, follow_redirects=False, trust_env=False)


def grant(client, credentials, **parameters):
    response = client.post(TOKEN, data={**credentials, **parameters})
    response.raise_for_status()
    data = response.json()
    access = _token_field(data, 'access_token')
    metadata = data
    if 'hub_id' not in data or 'scopes' not in data:
        check = client.post(TOKEN + '/introspect', data={
            **credentials, 'token': access, 'token_type_hint': 'access_token',
        })
        check.raise_for_status()
        metadata = check.json()
        if metadata.get('active') is False:
            raise ValueError('Inactive HubSpot token')
    if str(metadata['hub_id']) != PORTAL or set(metadata['scopes']) != set(SCOPES):
        raise ValueError('Unexpected HubSpot account or scopes')
    return access, _token_field(data, 'refresh_token') if 'refresh_token' in data else None


def records(client, access, resource, fields):
    response = client.get(API + '/crm/v3/objects/' + resource,
                          headers={'Authorization': 'Bearer ' + access},
                          params={'limit': 10, 'properties': ','.join(fields)})
    response.raise_for_status()
    data = response.json()
    return {
        'rows': [{field: item['properties'].get(field) for field in fields}
                 for item in data['results']],
        'has_more': bool(data.get('paging', {}).get('next')),
    }


def register(router, current_user, origin, require_manager, session, store, protect, reveal):
    def saved(workspace, connection):
        return connection.execute("""SELECT credential FROM connections
            WHERE workspace=? AND source=? AND property_id=?""",
                                  (str(workspace), SOURCE, PORTAL)).fetchone()

    def label(workspace):
        return json.dumps([str(workspace), SOURCE, PORTAL], separators=(',', ':'))

    @router.get(RESOURCE)
    def status(workspace: UUID, user=Depends(current_user)):
        require_manager(workspace, user)
        try:
            config()
        except HTTPException:
            return {'configured': False, 'portal_id': None}
        with store() as connection:
            row = saved(workspace, connection)
        return {'configured': True, 'portal_id': PORTAL if row else None}

    @router.post(RESOURCE + '/start')
    def start(workspace: UUID, request: Request, user=Depends(current_user)):
        origin(request)
        require_manager(workspace, user)
        credentials = config()
        state = secrets.token_urlsafe(32)
        key = activation.digest(state)
        sid = session(request)
        now = int(time())
        with store() as connection:
            connection.execute('DELETE FROM pending WHERE expires_at<=? OR (session_digest=? AND phase=?)',
                               (now, sid, PHASE))
            connection.execute('INSERT INTO pending VALUES (?,?,?,?,?)',
                               (key, PHASE, sid, now+EXPIRY, protect(str(workspace), key)))
        response = JSONResponse({'url': 'https://app.hubspot.com/oauth/authorize?' + urlencode({
            'client_id': credentials['client_id'], 'redirect_uri': CALLBACK,
            'scope': ' '.join(SCOPES), 'state': state,
        })}, headers={'Cache-Control': 'no-store'})
        response.set_cookie(COOKIE, state, max_age=EXPIRY, secure=True, httponly=True, samesite='lax')
        return response

    @router.get('/oauth/hubspot/callback')
    def finish(request: Request):
        def back(result):
            response = RedirectResponse('/?hubspot=' + result + '#connected', status_code=303,
                                        headers={'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'})
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='lax')
            return response

        state = request.query_params.get('state', '')
        cookie = request.cookies.get(COOKIE, '')
        if len(state) != 43 or not hmac.compare_digest(state.encode(), cookie.encode()):
            return back('expired')
        try:
            user = current_user(request)
            key = activation.digest(state)
            with store() as connection:
                row = connection.execute("""DELETE FROM pending WHERE id=? AND phase=?
                    AND session_digest=? AND expires_at>? RETURNING payload""",
                                         (key, PHASE, session(request), int(time()))).fetchone()
            if not row:
                return back('expired')
            workspace = reveal(row[0], key)
            require_manager(UUID(workspace), user)
            code = request.query_params.get('code', '')
            if not 1 <= len(code) <= 2048 or request.query_params.get('error'):
                return back('denied')
            with provider() as client:
                _, refresh = grant(client, config(), grant_type='authorization_code',
                                   redirect_uri=CALLBACK, code=code)
            if not refresh:
                raise ValueError('Missing HubSpot refresh token')
            with store() as connection:
                connection.execute("""INSERT INTO connections VALUES (?,?,?,?,?)
                    ON CONFLICT(workspace,source,property_id) DO UPDATE
                    SET credential=excluded.credential""",
                                   (workspace, SOURCE, PORTAL, 'HubSpot', protect(refresh, label(workspace))))
        except (HTTPException, httpx.HTTPError, ValueError, KeyError, TypeError):
            return back('failed')
        return back('connected')

    @router.get(RESOURCE + '/summary')
    def summary(workspace: UUID, user=Depends(current_user)):
        require_manager(workspace, user)
        credentials = config()
        with store() as connection:
            row = saved(workspace, connection)
        if not row:
            raise HTTPException(404, 'HubSpot is not connected')
        try:
            refresh = reveal(row[0], label(workspace))
            with provider() as client:
                access, rotated = grant(client, credentials, grant_type='refresh_token', refresh_token=refresh)
                if rotated and rotated != refresh:
                    with store() as connection:
                        connection.execute("""UPDATE connections SET credential=?
                            WHERE workspace=? AND source=? AND property_id=? AND credential=?""",
                                           (protect(rotated, label(workspace)), str(workspace), SOURCE, PORTAL, row[0]))
                data = {
                    'contacts': records(client, access, 'contacts', ('firstname', 'lastname')),
                    'deals': records(client, access, 'deals', ('dealname',)),
                }
            return JSONResponse(data, headers={'Cache-Control': 'no-store'})
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, 'HubSpot CRM data is unavailable') from None
