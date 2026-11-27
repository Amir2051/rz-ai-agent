"""Marketing-specific authentication and control-center service.

Private preview activation: password and Google login, mandatory owner MFA with
recovery codes, tenant-scoped drafts/settings/activity, and the protected MCP
resource with its OAuth 2.1 authorization server.
"""
import hashlib, hmac, os, secrets
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
import psycopg

from auth_service import activation, customer_registration, google_login, google_verify

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
COOKIE = '__Host-mi_session'
CHALLENGE = '__Host-mi_challenge'
hasher = PasswordHasher()
DUMMY = hasher.hash(secrets.token_urlsafe(32))
ROOT = Path(__file__).resolve().parents[1]


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _identity():
    user = os.getenv('MI_DB_USER', 'marketing_service')
    name = os.getenv('MI_DB_NAME', 'marketing_intelligence')
    return user, name


@contextmanager
def db(role='marketing_auth'):
    url = os.getenv('MI_DATABASE_URL', '')
    if not url:
        raise HTTPException(503, 'Authentication not configured')
    with psycopg.connect(url, connect_timeout=5, sslmode='verify-full',
                         sslrootcert='/etc/ssl/certs/ca-certificates.crt') as conn:
        if not conn.pgconn.ssl_in_use or conn.execute('SELECT session_user, current_database()').fetchone() != _identity():
            raise HTTPException(503, 'Database identity not permitted')
        conn.execute('SET LOCAL ROLE ' + role)
        yield conn


def origin(request):
    expected = os.getenv('MI_APP_ORIGIN', '')
    if not expected.startswith('https://') or request.headers.get('origin') != expected:
        raise HTTPException(403, 'Origin not allowed')


def throttle(email):
    with db() as conn:
        for bucket in ('global', 'email:' + digest(email)):
            row = conn.execute("""INSERT INTO auth_login_limits(bucket,attempts,window_start)
                VALUES (%s,1,now()) ON CONFLICT(bucket) DO UPDATE SET
                attempts=CASE WHEN auth_login_limits.window_start < now()-interval '15 minutes' THEN 1 ELSE auth_login_limits.attempts+1 END,
                window_start=CASE WHEN auth_login_limits.window_start < now()-interval '15 minutes' THEN now() ELSE auth_login_limits.window_start END
                RETURNING attempts""", (bucket,)).fetchone()
            if row[0] > (100 if bucket == 'global' else 10):
                raise HTTPException(429, 'Too many attempts')


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


class Registration(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


def current_user(request: Request):
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 200:
        raise HTTPException(401, 'Sign in required')
    with db() as conn:
        row = conn.execute(
            "SELECT u.id::text,u.email FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id "
            "WHERE s.token_hash=%s AND s.expires_at>now() AND u.disabled_at IS NULL "
            "AND GREATEST(s.last_seen_at,s.created_at)>now()-interval '30 minutes'",
            (digest(token),),
        ).fetchone()
        if row:
            conn.execute('UPDATE auth_sessions SET last_seen_at=now() WHERE token_hash=%s', (digest(token),))
    if not row:
        raise HTTPException(401, 'Sign in required')
    return {'id': row[0], 'email': row[1]}


@app.middleware('http')
async def security(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if response.status_code == 401 and request.url.path.startswith('/mcp'):
        response.headers['WWW-Authenticate'] = (
            'Bearer resource_metadata="' + os.getenv('MI_APP_ORIGIN', '').rstrip('/') +
            '/.well-known/oauth-protected-resource/mcp", scope="marketing:read"')
    return response


@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    try:
        current_user(request)
    except HTTPException as e:
        if e.status_code == 401:
            return (ROOT / 'control-center/login.html').read_text().replace('data-google-enabled="0"', 'data-google-enabled="1"' if os.getenv('MI_LOGIN_ENABLED') == '1' and os.getenv('MI_REGISTRATION_ENABLED') == '1' and os.getenv('MI_GOOGLE_CLIENT_JSON') else 'data-google-enabled="0"')
        raise
    return (ROOT / 'control-center/index.html').read_text()


def _issue_session(conn, user_id, response):
    token = secrets.token_urlsafe(32)
    conn.execute(
        'INSERT INTO auth_sessions(user_id,token_hash,expires_at,last_seen_at) VALUES (%s,%s,%s,now())',
        (str(user_id), digest(token), datetime.now(timezone.utc) + timedelta(hours=8)),
    )
    response.set_cookie(COOKIE, token, max_age=28800, secure=True, httponly=True, samesite='lax', path='/')
    return {'authenticated': True}


def _challenge(response, token, owner_enrollment=False):
    response.set_cookie(CHALLENGE, token, max_age=600, secure=True, httponly=True, samesite='lax', path='/')


@app.post('/v1/auth/register', status_code=201)
def register(payload: dict, request: Request):
    if os.getenv('MI_REGISTRATION_ENABLED') != '1':
        raise HTTPException(404, 'Registration not available')
    origin(request)
    payload = Registration(**payload)
    email = payload.email.strip().lower()
    if '@' not in email:
        raise HTTPException(422, 'Invalid email')
    secret_hash = hasher.hash(payload.password)
    try:
        with db('marketing_registration') as conn:
            user_id = customer_registration.provision_password(conn, email, secret_hash)
    except ValueError as e:
        raise HTTPException(409, 'Account cannot be created') from None
    return {'created': True, 'user': user_id}


@app.post('/v1/auth/login')
def login(payload: Credentials, request: Request, response: Response):
    origin(request)
    if os.getenv('MI_LOGIN_ENABLED') != '1':
        raise HTTPException(503, 'Login not activated')
    email = payload.email.strip().lower()
    throttle(email)
    with db() as conn:
        row = conn.execute('SELECT id::text,email,password_hash FROM auth_users WHERE email=%s AND disabled_at IS NULL', (email,)).fetchone()
        try:
            valid = hasher.verify(row[2] if row else DUMMY, payload.password)
        except VerificationError:
            valid = False
        if not row or not valid:
            raise HTTPException(401, 'Invalid credentials')
        if activation.confirmation_required(conn, row[0], row[1]):
            mfa_row = conn.execute('SELECT confirmed_at FROM auth_mfa WHERE user_id=%s', (row[0],)).fetchone()
            owner_enrollment = row[1].lower() == 'yayamoussa2050@gmail.com' and not (mfa_row and mfa_row[0])
            token = activation.create_challenge(conn, row[0], owner_enrollment=owner_enrollment)
            _challenge(response, token)
            return {'mfa_required': True, 'owner_enrollment': owner_enrollment}
        return _issue_session(conn, row[0], response)


def _consume(request, response):
    token = request.cookies.get(CHALLENGE, '')
    with db() as conn:
        record = activation.consume_challenge(conn, token)
    if not record:
        raise HTTPException(401, 'MFA challenge expired')
    return record


@app.post('/v1/auth/mfa/enroll')
def mfa_enroll(request: Request, response: Response):
    origin(request)
    record = _consume(request, response)
    if not record['owner_enrollment']:
        raise HTTPException(403, 'Enrollment not authorized')
    with db() as conn:
        secret = activation.enroll(conn, record['user_id'])
        _challenge(response, activation.create_challenge(conn, record['user_id'], owner_enrollment=True))
    uri = ('otpauth://totp/SafeNestT%20Marketing:' + record['user_id'] +
           '?secret=' + secret + '&issuer=SafeNestT%20Marketing&algorithm=SHA1&digits=6&period=30')
    return {'enrollment_uri': uri, 'requires_confirmation': True}


@app.post('/v1/auth/mfa/confirm')
def mfa_confirm(payload: dict, request: Request, response: Response):
    origin(request)
    record = _consume(request, response)
    if not record['owner_enrollment']:
        raise HTTPException(403, 'Enrollment not authorized')
    with db() as conn:
        codes = activation.confirm(conn, record['user_id'], payload.get('code', ''))
    if not codes:
        raise HTTPException(400, 'Invalid verification code')
    with db() as conn:
        result = _issue_session(conn, record['user_id'], response)
    response.delete_cookie(CHALLENGE, path='/', secure=True, httponly=True, samesite='lax')
    return {**result, 'recovery_codes': codes}


@app.post('/v1/auth/mfa/verify')
def mfa_verify(payload: dict, request: Request, response: Response):
    origin(request)
    record = _consume(request, response)
    with db() as conn:
        row = conn.execute('SELECT email FROM auth_users WHERE id=%s', (record['user_id'],)).fetchone()
        owner = bool(row and row[0].lower() == 'yayamoussa2050@gmail.com')
        confirmed = conn.execute('SELECT confirmed_at FROM auth_mfa WHERE user_id=%s', (record['user_id'],)).fetchone()
        if owner and not (confirmed and confirmed[0]):
            raise HTTPException(403, 'Owner MFA enrollment required')
        if not activation.verify(conn, record['user_id'], payload.get('code', '')):
            raise HTTPException(400, 'Invalid MFA code or recovery code')
        result = _issue_session(conn, record['user_id'], response)
    response.delete_cookie(CHALLENGE, path='/', secure=True, httponly=True, samesite='lax')
    return result


@app.get('/v1/auth/me')
def me(user=Depends(current_user)):
    return user


@app.post('/v1/auth/logout')
def logout(request: Request, response: Response):
    origin(request)
    token = request.cookies.get(COOKIE, '')
    if token:
        with db() as conn:
            conn.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (digest(token),))
    response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='lax')
    return {'authenticated': False}


@app.post('/v1/auth/logout-all')
def logout_all(request: Request, response: Response, user=Depends(current_user)):
    origin(request)
    with db() as conn:
        conn.execute('DELETE FROM auth_sessions WHERE user_id=%s', (user['id'],))
    response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='lax')
    return {'authenticated': False}


@app.get('/oauth/google/start')
def google_start():
    if os.getenv('MI_LOGIN_ENABLED') != '1' or os.getenv('MI_REGISTRATION_ENABLED') != '1':
        raise HTTPException(503, 'Google sign-in not activated')
    path = os.getenv('MI_GOOGLE_CLIENT_JSON', '')
    if not path:
        raise HTTPException(503, 'Google sign-in not configured')
    try:
        config = google_login.config(path)
        prepared = google_login.prepare(config['client_id'])
        with db() as conn:
            activation.create_oidc_attempt(conn, prepared['state'], prepared['nonce'], prepared['verifier'])
    except (ValueError, OSError):
        raise HTTPException(503, 'Google sign-in not configured') from None
    redirect = RedirectResponse(prepared['url'], status_code=303)
    redirect.set_cookie('__Host-mi_google_state', prepared['state'], max_age=600,
                        secure=True, httponly=True, samesite='lax', path='/')
    return redirect


@app.get('/oauth/google/callback')
def google_callback(request: Request, response: Response):
    if os.getenv('MI_LOGIN_ENABLED') != '1' or os.getenv('MI_REGISTRATION_ENABLED') != '1':
        raise HTTPException(503, 'Google sign-in not activated')
    q = request.query_params
    if q.get('error'):
        raise HTTPException(400, 'Google authorization denied')
    state, code = q.get('state', ''), q.get('code', '')
    browser_state = request.cookies.get('__Host-mi_google_state', '')
    if not state or not code or not browser_state or not hmac.compare_digest(state.encode(), browser_state.encode()):
        raise HTTPException(400, 'Invalid authorization state')
    path = os.getenv('MI_GOOGLE_CLIENT_JSON', '')
    if not path:
        raise HTTPException(503, 'Google sign-in not configured')
    try:
        config = google_login.config(path)
    except (ValueError, OSError):
        raise HTTPException(503, 'Google sign-in not configured') from None
    with db() as conn:
        attempt = activation.consume_oidc_attempt(conn, state)
    if not attempt:
        raise HTTPException(400, 'Invalid authorization state')
    try:
        subject, email = google_verify.exchange(code, attempt[1], attempt[0], config, allow_owner=True)
    except Exception:
        raise HTTPException(400, 'Google token exchange failed') from None
    try:
        with db('marketing_registration') as conn:
            user_id = customer_registration.provision(conn, subject, email, hasher.hash(secrets.token_urlsafe(32)))
    except ValueError:
        raise HTTPException(400, 'Identity not eligible') from None
    with db() as conn:
        redirect = RedirectResponse('/', status_code=303)
        if activation.confirmation_required(conn, user_id, email):
            redirect = RedirectResponse('/?mfa=verify', status_code=303)
            _challenge(redirect, activation.create_challenge(conn, user_id))
        else:
            _issue_session(conn, user_id, redirect)
        redirect.delete_cookie('__Host-mi_google_state', path='/', secure=True, httponly=True, samesite='lax')
        return redirect


@app.get('/v1/workspaces')
def workspaces(user=Depends(current_user)):
    with db() as conn:
        conn.execute('SET LOCAL ROLE marketing_reader')
        conn.execute("SELECT set_config('app.user_id',%s,true)", (user['id'],))
        rows = conn.execute('SELECT id::text,name FROM workspaces ORDER BY name').fetchall()
    return [{'id': r[0], 'name': r[1]} for r in rows]


from auth_service.drafts import DraftInput, build
from marketing.storage import save, list_drafts


@app.get('/v1/workspaces/{workspace}/drafts')
def drafts_list(workspace: UUID, user=Depends(current_user)):
    with db() as conn:
        return list_drafts(conn, user['id'], str(workspace))


@app.post('/v1/workspaces/{workspace}/drafts', status_code=201)
def drafts_create(workspace: UUID, payload: DraftInput, request: Request, user=Depends(current_user)):
    origin(request)
    record = build(workspace, payload)
    try:
        with db() as conn:
            return save(conn, user['id'], str(workspace), payload.kind, record)
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(404, 'Workspace not available') from None


from auth_service import ai_settings


@app.get('/v1/workspaces/{workspace}/ai-settings')
def get_ai_settings(workspace: UUID, user=Depends(current_user)):
    with db() as conn:
        return ai_settings.get(conn, user['id'], str(workspace))


@app.put('/v1/workspaces/{workspace}/ai-settings')
def put_ai_settings(workspace: UUID, payload: ai_settings.Settings, request: Request, user=Depends(current_user)):
    origin(request)
    try:
        with db() as conn:
            return ai_settings.save(conn, user['id'], str(workspace), payload)
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(404, 'Workspace not available') from None


from marketing.mcp_activity import history


@app.get('/v1/workspaces/{workspace}/activity')
def activity(workspace: UUID, user=Depends(current_user)):
    with db() as conn:
        return history(conn, user['id'], str(workspace))


from gateway.oauth_server import install as install_oauth
install_oauth(app, db)

from gateway.runtime import build as build_mcp
app.mount('/mcp', build_mcp(db).http_app(path='/'))
