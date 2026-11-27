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
GOOGLE_STATE = '__Host-mi_google_state'
MFA_ATTEMPT_LIMIT = 5
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
        user = current_user(request)
    except HTTPException as e:
        if e.status_code == 401:
            return (ROOT / 'control-center/login.html').read_text().replace('data-google-enabled="0"', 'data-google-enabled="1"' if _google_available() else 'data-google-enabled="0"')
        raise
    dashboard = (ROOT / 'control-center/index.html').read_text()
    if user['email'].lower() == activation.OWNER and _google_available():
        with db('marketing_registration') as conn:
            linked = conn.execute(
                "SELECT 1 FROM auth_external_identities WHERE provider='google' AND user_id=%s",
                (user['id'],),
            ).fetchone()
        element = 'google-linked' if linked else 'google-link'
        dashboard = dashboard.replace(f'id="{element}" hidden', f'id="{element}"', 1)
    return dashboard


def _issue_session(conn, user_id, response):
    token = secrets.token_urlsafe(32)
    conn.execute(
        'INSERT INTO auth_sessions(user_id,token_hash,expires_at,last_seen_at) VALUES (%s,%s,%s,now())',
        (str(user_id), digest(token), datetime.now(timezone.utc) + timedelta(hours=8)),
    )
    response.set_cookie(COOKIE, token, max_age=28800, secure=True, httponly=True, samesite='lax', path='/')
    return {'authenticated': True}


def _challenge(response, token):
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
    if attempt.owner_session_hash is not None:
        return _complete_owner_link(attempt.owner_session_hash, subject, email)
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
            owner_enrollment = (row[1].lower() == activation.OWNER
                                and not activation.has_confirmed_mfa(conn, row[0]))
            token = activation.create_challenge(conn, row[0], owner_enrollment=owner_enrollment)
            _challenge(response, token)
            return {'mfa_required': True, 'owner_enrollment': owner_enrollment}
        return _issue_session(conn, row[0], response)


def _clear_cookie(response, name):
    response.delete_cookie(name, path='/', secure=True, httponly=True, samesite='lax')


def _challenge_account(conn, request):
    record = activation.consume_challenge(conn, request.cookies.get(CHALLENGE, ''))
    if not record:
        raise HTTPException(401, 'MFA challenge expired')
    active = conn.execute(
        'SELECT 1 FROM auth_users WHERE id=%s AND disabled_at IS NULL',
        (record['user_id'],),
    ).fetchone()
    if not active:
        raise HTTPException(401, 'Sign in required')
    return record


def _mfa_attempt_allowed(conn, user_id):
    attempts, = conn.execute(
        """INSERT INTO auth_login_limits(bucket,attempts,window_start)
           VALUES (%s,1,now()) ON CONFLICT(bucket) DO UPDATE SET
             attempts=CASE WHEN auth_login_limits.window_start <= now()-interval '15 minutes'
                           THEN 1 ELSE LEAST(auth_login_limits.attempts,%s)+1 END,
             window_start=CASE WHEN auth_login_limits.window_start <= now()-interval '15 minutes'
                               THEN now() ELSE auth_login_limits.window_start END
           RETURNING attempts""",
        ('mfa:' + digest(user_id), MFA_ATTEMPT_LIMIT),
    ).fetchone()
    return attempts <= MFA_ATTEMPT_LIMIT


@app.post('/v1/auth/mfa/enroll')
def mfa_enroll(request: Request, response: Response):
    origin(request)
    with db() as conn:
        record = _challenge_account(conn, request)
        if not record['owner_enrollment'] or activation.has_confirmed_mfa(conn, record['user_id']):
            raise HTTPException(403, 'Enrollment not authorized')
        secret = activation.enroll(conn, record['user_id'])
        _challenge(response, activation.create_challenge(conn, record['user_id'], owner_enrollment=True))
    uri = ('otpauth://totp/SafeNestT%20Marketing:' + record['user_id'] +
           '?secret=' + secret + '&issuer=SafeNestT%20Marketing&algorithm=SHA1&digits=6&period=30')
    return {'enrollment_uri': uri, 'requires_confirmation': True}


def _finish_mfa(request, response, code, *, enrollment):
    origin(request)
    with db() as conn:
        record = _challenge_account(conn, request)
        user_id = record['user_id']
        if record['owner_enrollment'] != enrollment:
            raise HTTPException(403, 'MFA challenge purpose mismatch')
        if not enrollment and not activation.has_confirmed_mfa(conn, user_id):
            raise HTTPException(403, 'Confirmed MFA is required')
        allowed = _mfa_attempt_allowed(conn, user_id)
        if allowed:
            verified = (activation.confirm(conn, user_id, code) if enrollment
                        else activation.verify(conn, user_id, code))
            if verified:
                result = _issue_session(conn, user_id, response)
                if enrollment:
                    result['recovery_codes'] = verified
                _clear_cookie(response, CHALLENGE)
                return result
    # Return failures after the transaction commits the counted attempt and challenge consumption.
    if not allowed:
        raise HTTPException(429, 'Too many MFA attempts', headers={'Retry-After': '900'})
    raise HTTPException(400, 'Invalid MFA code or recovery code')


@app.post('/v1/auth/mfa/confirm')
def mfa_confirm(payload: dict, request: Request, response: Response):
    return _finish_mfa(request, response, payload.get('code', ''), enrollment=True)


@app.post('/v1/auth/mfa/verify')
def mfa_verify(payload: dict, request: Request, response: Response):
    return _finish_mfa(request, response, payload.get('code', ''), enrollment=False)


@app.get('/v1/auth/me')
def me(user=Depends(current_user)):
    return user


@app.post('/v1/auth/logout')
def logout(request: Request, response: Response):
    origin(request)
    token = request.cookies.get(COOKIE, '')
    pending = request.cookies.get(CHALLENGE, '')
    google_state = request.cookies.get(GOOGLE_STATE, '')
    if token or pending or google_state:
        with db() as conn:
            if token:
                conn.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (digest(token),))
            if pending:
                activation.consume_challenge(conn, pending)
            if google_state:
                activation.consume_oidc_attempt(conn, google_state)
    for name in (COOKIE, CHALLENGE, GOOGLE_STATE):
        _clear_cookie(response, name)
    return {'authenticated': False}


@app.post('/v1/auth/logout-all')
def logout_all(request: Request, response: Response, user=Depends(current_user)):
    origin(request)
    with db() as conn:
        conn.execute('DELETE FROM auth_sessions WHERE user_id=%s', (user['id'],))
    response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='lax')
    return {'authenticated': False}


def _google_available():
    return bool(os.getenv('MI_LOGIN_ENABLED') == '1'
                and os.getenv('MI_REGISTRATION_ENABLED') == '1'
                and os.getenv('MI_GOOGLE_CLIENT_JSON'))


def _google_config():
    if os.getenv('MI_LOGIN_ENABLED') != '1' or os.getenv('MI_REGISTRATION_ENABLED') != '1':
        raise HTTPException(503, 'Google sign-in not activated')
    path = os.getenv('MI_GOOGLE_CLIENT_JSON', '')
    if not path:
        raise HTTPException(503, 'Google sign-in not configured')
    try:
        return google_login.config(path)
    except (ValueError, OSError, KeyError, TypeError):
        raise HTTPException(503, 'Google sign-in not configured') from None


def _google_state_cookie(response, state):
    response.set_cookie(GOOGLE_STATE, state, max_age=600,
                        secure=True, httponly=True, samesite='lax', path='/')


@app.get('/oauth/google/start')
def google_start():
    prepared = google_login.prepare(_google_config()['client_id'])
    with db() as conn:
        activation.create_oidc_attempt(conn, prepared['state'], prepared['nonce'], prepared['verifier'])
    redirect = RedirectResponse(prepared['url'], status_code=303)
    _google_state_cookie(redirect, prepared['state'])
    return redirect


class OwnerLinkProof(BaseModel):
    password: str = Field(min_length=12, max_length=256)
    code: str = Field(pattern=r'^[0-9]{6}$')


@app.post('/v1/auth/google/link')
def google_link_start(payload: OwnerLinkProof, request: Request, response: Response,
                      user=Depends(current_user)):
    origin(request)
    if user['email'].lower() != activation.OWNER:
        raise HTTPException(403, 'Owner account required')
    prepared = google_login.prepare(_google_config()['client_id'])
    throttle(activation.OWNER)
    with db() as conn:
        row = conn.execute(
            'SELECT password_hash FROM auth_users WHERE id=%s AND email=%s AND disabled_at IS NULL',
            (user['id'], activation.OWNER),
        ).fetchone()
        try:
            valid_password = hasher.verify(row[0] if row else DUMMY, payload.password)
        except VerificationError:
            valid_password = False
        if not row or not valid_password:
            raise HTTPException(401, 'Account verification failed')
        allowed = _mfa_attempt_allowed(conn, user['id'])
        if allowed and activation.verify(conn, user['id'], payload.code):
            activation.create_oidc_attempt(
                conn, prepared['state'], prepared['nonce'], prepared['verifier'],
                owner_session_hash=digest(request.cookies[COOKIE]),
            )
            _google_state_cookie(response, prepared['state'])
            return {'authorization_url': prepared['url']}
    if not allowed:
        raise HTTPException(429, 'Too many MFA attempts', headers={'Retry-After': '900'})
    raise HTTPException(401, 'Account verification failed')


def _complete_owner_link(session_hash, subject, email):
    try:
        with db() as conn:
            # Lock the live session until the link commits, so concurrent logout
            # and linking cannot both proceed using a revoked session.
            row = conn.execute(
                """UPDATE auth_sessions SET last_seen_at=now()
                   WHERE token_hash=%s AND expires_at>now()
                     AND GREATEST(last_seen_at,created_at)>now()-interval '30 minutes'
                   RETURNING user_id::text""",
                (session_hash,),
            ).fetchone()
            if not row:
                raise HTTPException(401, 'Sign in required')
            conn.execute('SET LOCAL ROLE marketing_registration')
            customer_registration.link_owner(conn, row[0], subject, email)
    except ValueError:
        raise HTTPException(400, 'Identity cannot be linked') from None
    redirect = RedirectResponse('/', status_code=303)
    _clear_cookie(redirect, GOOGLE_STATE)
    return redirect


@app.get('/oauth/google/callback')
def google_callback(request: Request):
    if os.getenv('MI_LOGIN_ENABLED') != '1' or os.getenv('MI_REGISTRATION_ENABLED') != '1':
        raise HTTPException(503, 'Google sign-in not activated')
    q = request.query_params
    if q.get('error'):
        raise HTTPException(400, 'Google authorization denied')
    state, code = q.get('state', ''), q.get('code', '')
    browser_state = request.cookies.get(GOOGLE_STATE, '')
    if not state or not code or not browser_state or not hmac.compare_digest(state.encode(), browser_state.encode()):
        raise HTTPException(400, 'Invalid authorization state')
    config = _google_config()
    with db() as conn:
        attempt = activation.consume_oidc_attempt(conn, state)
    if not attempt:
        raise HTTPException(400, 'Invalid authorization state')
    if attempt.owner_session_hash is not None:
        session = request.cookies.get(COOKIE, '')
        if not session or not hmac.compare_digest(digest(session), attempt.owner_session_hash):
            raise HTTPException(401, 'Sign in required')
    try:
        subject, email = google_verify.exchange(code, attempt.verifier, attempt.nonce, config, allow_owner=True)
    except Exception:
        raise HTTPException(400, 'Google token exchange failed') from None
    if attempt.owner_session_hash is not None:
        return _complete_owner_link(attempt.owner_session_hash, subject, email)
    try:
        with db('marketing_registration') as conn:
            user_id = customer_registration.provision(conn, subject, email, hasher.hash(secrets.token_urlsafe(32)))
    except ValueError:
        raise HTTPException(400, 'Identity not eligible') from None
    with db() as conn:
        redirect = RedirectResponse('/', status_code=303)
        if activation.confirmation_required(conn, user_id, email):
            redirect = RedirectResponse('/?mfa=verify', status_code=303)
            previous_session = request.cookies.get(COOKIE, '')
            if previous_session:
                conn.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (digest(previous_session),))
            _clear_cookie(redirect, COOKIE)
            _challenge(redirect, activation.create_challenge(conn, user_id))
        else:
            _issue_session(conn, user_id, redirect)
            _clear_cookie(redirect, CHALLENGE)
        _clear_cookie(redirect, GOOGLE_STATE)
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
