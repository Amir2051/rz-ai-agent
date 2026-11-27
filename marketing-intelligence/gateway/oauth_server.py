"""OAuth 2.1 authorization server for the protected MCP resource.

Implements RFC 8414 discovery, RFC 9728 protected-resource metadata, RFC 7591
client registration, authorization-code + PKCE S256, audience-bound opaque
tokens, refresh rotation with reuse detection, and RFC 7009 revocation.
"""
import base64, hashlib, hmac, os, re, secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import urlencode, urlparse, parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def issuer_url():
    return (os.getenv('MI_APP_ORIGIN', '').strip() or 'https://marketing.safenestt.com').rstrip('/')


def resource_url():
    return (os.getenv('MI_MCP_RESOURCE', '').strip() or issuer_url() + '/mcp')


def _oauth(conn):
    conn.execute('SET LOCAL ROLE marketing_oauth')


def _valid_redirect(uri):
    try:
        p = urlparse(uri)
    except Exception:
        return False
    if p.fragment or p.username or p.password:
        return False
    if p.scheme == 'https':
        return bool(p.netloc)
    if p.scheme == 'http':
        return p.hostname in ('127.0.0.1', 'localhost', '::1')
    return False


def _client_auth(db, client_id, client_secret, authorization):
    with db() as conn:
        _oauth(conn)
        row = conn.execute('SELECT client_secret_hash,redirect_uris FROM oauth_clients WHERE client_id=%s', (client_id,)).fetchone()
        if not row:
            raise HTTPException(401, 'invalid_client')
        expected = row[0]
        supplied = client_secret
        if not supplied and authorization and authorization.lower().startswith('basic '):
            try:
                raw = base64.b64decode(authorization.split(None, 1)[1]).decode()
                user, secret = raw.split(':', 1)
                if user == client_id:
                    supplied = secret
            except Exception:
                supplied = None
        if not expected or not supplied or not hmac.compare_digest(expected, digest(supplied)):
            raise HTTPException(401, 'invalid_client')
        return list(row[1])


def _workspace_for(db, user_id):
    with db() as conn:
        conn.execute('SET LOCAL ROLE marketing_reader')
        conn.execute("SELECT set_config('app.user_id',%s,true)", (str(user_id),))
        row = conn.execute('SELECT id::text FROM workspaces ORDER BY name LIMIT 1').fetchone()
    if not row:
        raise HTTPException(403, 'No workspace available')
    return row[0]


def install(app, db):
    router = APIRouter()

    @router.get('/.well-known/oauth-protected-resource')
    @router.get('/.well-known/oauth-protected-resource/mcp')
    def protected_resource():
        return {'resource': resource_url(), 'authorization_servers': [issuer_url()],
                'scopes_supported': ['marketing:read'],
                'bearer_methods_supported': ['header']}

    @router.get('/.well-known/oauth-authorization-server')
    def authorization_server():
        issuer = issuer_url()
        return {'issuer': issuer,
                'authorization_endpoint': issuer + '/oauth/authorize',
                'token_endpoint': issuer + '/oauth/token',
                'registration_endpoint': issuer + '/oauth/register',
                'revocation_endpoint': issuer + '/oauth/revoke',
                'response_types_supported': ['code'],
                'grant_types_supported': ['authorization_code', 'refresh_token'],
                'code_challenge_methods_supported': ['S256'],
                'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
                'scopes_supported': ['marketing:read'],
                'authorization_response_iss_parameter_supported': True}

    @router.post('/oauth/register', status_code=201)
    async def register(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_request')
        name = body.get('client_name', '')
        uris = body.get('redirect_uris', [])
        if not isinstance(name, str) or not 1 <= len(name) <= 200 or not isinstance(uris, list) or not uris:
            raise HTTPException(400, 'invalid_client_metadata')
        if len(uris) > 10 or any(not isinstance(u, str) or not _valid_redirect(u) for u in uris):
            raise HTTPException(400, 'invalid_redirect_uri')
        client_id, secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        with db() as conn:
            _oauth(conn)
            conn.execute('INSERT INTO oauth_clients(client_id,client_secret_hash,name,redirect_uris) VALUES (%s,%s,%s,%s)',
                         (client_id, digest(secret), name, uris))
        return {'client_id': client_id, 'client_secret': secret, 'client_name': name,
                'redirect_uris': uris, 'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'], 'token_endpoint_auth_method': 'client_secret_post'}

    @router.get('/oauth/authorize')
    async def authorize(request: Request):
        from auth_service.app import current_user
        try:
            user = current_user(request)
        except HTTPException:
            login = issuer_url() + '/'
            return RedirectResponse(login, status_code=303)
        q = dict(request.query_params)
        if q.get('response_type') != 'code':
            raise HTTPException(400, 'unsupported_response_type')
        state = q.get('state', '')
        if not isinstance(state, str) or not 20 <= len(state) <= 256:
            raise HTTPException(400, 'invalid_state')
        challenge = q.get('code_challenge', '')
        if q.get('code_challenge_method') != 'S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', challenge):
            raise HTTPException(400, 'PKCE S256 required')
        resource = q.get('resource', resource_url())
        if resource != resource_url():
            raise HTTPException(400, 'invalid_resource')
        scopes = [s for s in q.get('scope', 'marketing:read').split() if s]
        if not scopes or any(s != 'marketing:read' for s in scopes):
            raise HTTPException(400, 'invalid_scope')
        with db() as conn:
            _oauth(conn)
            row = conn.execute('SELECT redirect_uris FROM oauth_clients WHERE client_id=%s', (q.get('client_id', ''),)).fetchone()
        if not row or q.get('redirect_uri') not in list(row[0]):
            raise HTTPException(400, 'invalid_client')
        code = secrets.token_urlsafe(32)
        workspace = _workspace_for(db, user['id'])
        with db() as conn:
            _oauth(conn)
            conn.execute(
                'INSERT INTO oauth_codes(code_hash,client_id,user_id,workspace_id,redirect_uri,'
                'code_challenge,resource,scopes,expires_at,family_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()+interval \'5 minutes\',%s)',
                (digest(code), q['client_id'], user['id'], workspace, q['redirect_uri'],
                 challenge, resource, scopes, str(uuid4())),
            )
        redirect = q['redirect_uri'] + ('&' if '?' in q['redirect_uri'] else '?') + urlencode(
            {'code': code, 'state': state, 'iss': issuer_url()})
        return RedirectResponse(redirect, status_code=302)

    async def _form(request):
        ctype = request.headers.get('content-type', '')
        if 'application/json' in ctype:
            try:
                return await request.json()
            except Exception:
                return {}
        form = await request.form()
        return dict(form)

    def _issue(conn, client_id, user_id, workspace, audience, scopes, family_id):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        conn.execute(
            'INSERT INTO oauth_tokens(token_hash,refresh_hash,client_id,user_id,workspace_id,audience,scopes,family_id,'
            'expires_at,refresh_expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()+interval \'15 minutes\',now()+interval \'30 days\')',
            (digest(access), digest(refresh), client_id, user_id, workspace, audience, scopes, str(family_id)),
        )
        return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer',
                'expires_in': 900, 'scope': ' '.join(scopes)}

    @router.post('/oauth/token')
    async def token(request: Request):
        body = await _form(request)
        grant = body.get('grant_type')
        client_id = body.get('client_id', '')
        redirects = _client_auth(db, client_id, body.get('client_secret'), request.headers.get('authorization'))
        with db() as conn:
            _oauth(conn)
            if grant == 'authorization_code':
                code = body.get('code', '')
                row = conn.execute(
                    'SELECT code_hash,user_id::text,workspace_id::text,redirect_uri,code_challenge,resource,scopes,family_id '
                    'FROM oauth_codes WHERE code_hash=%s AND consumed_at IS NULL AND expires_at>now()',
                    (digest(code),),
                ).fetchone()
                if not row or row[3] != body.get('redirect_uri') or row[5] != resource_url():
                    raise HTTPException(400, 'invalid_grant')
                verifier = body.get('code_verifier', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', verifier):
                    raise HTTPException(400, 'invalid_grant')
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                if not hmac.compare_digest(challenge, row[4]):
                    raise HTTPException(400, 'invalid_grant')
                consumed = conn.execute('UPDATE oauth_codes SET consumed_at=now() WHERE code_hash=%s AND consumed_at IS NULL RETURNING code_hash', (row[0],)).fetchone()
                if not consumed:
                    raise HTTPException(400, 'invalid_grant')
                return _issue(conn, client_id, row[1], row[2], row[5], list(row[6]), row[7])
            if grant == 'refresh_token':
                refresh = body.get('refresh_token', '')
                row = conn.execute(
                    'SELECT token_hash,user_id::text,workspace_id::text,audience,scopes,family_id,revoked_at,refresh_expires_at '
                    'FROM oauth_tokens WHERE refresh_hash=%s AND client_id=%s',
                    (digest(refresh), client_id),
                ).fetchone()
                if not row:
                    raise HTTPException(400, 'invalid_grant')
                if row[6] is not None or row[7] <= datetime.now(timezone.utc):
                    conn.execute('UPDATE oauth_tokens SET revoked_at=now() WHERE family_id=%s AND revoked_at IS NULL', (row[5],))
                    raise HTTPException(400, 'invalid_grant')
                conn.execute('UPDATE oauth_tokens SET revoked_at=now() WHERE token_hash=%s', (row[0],))
                return _issue(conn, client_id, row[1], row[2], row[3], list(row[4]), row[5])
        raise HTTPException(400, 'unsupported_grant_type')

    @router.post('/oauth/revoke', status_code=200)
    async def revoke(request: Request):
        body = await _form(request)
        token = body.get('token', '')
        if not isinstance(token, str) or not token:
            return JSONResponse({}, status_code=200)
        with db() as conn:
            _oauth(conn)
            conn.execute('UPDATE oauth_tokens SET revoked_at=now() WHERE token_hash=%s OR refresh_hash=%s',
                         (digest(token), digest(token)))
        return JSONResponse({}, status_code=200)

    app.include_router(router)
