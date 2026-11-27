"""Activation primitives: encrypted OIDC state, login challenges, MFA, recovery codes.

All secrets are encrypted with MI_TOKEN_ENCRYPTION_KEY (base64url, 32 bytes) and
tenant-bound AEAD labels. No secret value is logged or returned except the
one-time MFA provisioning URI and one-time recovery codes.
"""
import base64, hashlib, json, os, secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from auth_service import mfa
from auth_service.identity_policy import OWNER

OIDC_LABEL = b'safenestt-oidc-state-v1'
MFA_LABEL = b'safenestt-mfa-secret-v1'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _key():
    raw = os.getenv('MI_TOKEN_ENCRYPTION_KEY', '').strip()
    if not raw:
        raise RuntimeError('Token encryption key not configured')
    try:
        key = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
    except Exception:
        raise RuntimeError('Invalid token encryption key') from None
    if len(key) != 32:
        raise RuntimeError('Invalid token encryption key')
    return key


def seal(value, label):
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, value.encode(), label)


def unseal(blob, label):
    if not blob or len(blob) < 29:
        raise ValueError('Invalid encrypted record')
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], label).decode()


def create_oidc_attempt(conn, state, nonce, verifier):
    if not all(isinstance(x, str) and 20 <= len(x) <= 256 for x in (state, nonce, verifier)):
        raise ValueError('Invalid OIDC attempt')
    payload = seal(json.dumps({'nonce': nonce, 'verifier': verifier}, separators=(',', ':')), OIDC_LABEL)
    conn.execute(
        'INSERT INTO auth_oidc_attempts(state_hash,nonce_hash,encrypted_verifier,expires_at) '
        'VALUES (%s,%s,%s,now()+interval \'10 minutes\')',
        (digest(state), digest(nonce), payload),
    )


def consume_oidc_attempt(conn, state):
    if not isinstance(state, str) or not 20 <= len(state) <= 256:
        return None
    row = conn.execute(
        'UPDATE auth_oidc_attempts SET consumed_at=now() WHERE state_hash=%s '
        'AND consumed_at IS NULL AND expires_at>now() RETURNING nonce_hash,encrypted_verifier',
        (digest(state),),
    ).fetchone()
    if not row:
        return None
    try:
        values = json.loads(unseal(row[1], OIDC_LABEL))
    except Exception:
        return None
    if digest(values['nonce']) != row[0]:
        return None
    return values['nonce'], values['verifier']


def create_challenge(conn, user_id, owner_enrollment=False):
    token = secrets.token_urlsafe(32)
    conn.execute(
        'INSERT INTO auth_login_challenges(challenge_hash,user_id,expires_at,owner_enrollment) '
        'VALUES (%s,%s,now()+interval \'10 minutes\',%s)',
        (digest(token), str(user_id), bool(owner_enrollment)),
    )
    return token


def consume_challenge(conn, token):
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        return None
    row = conn.execute(
        'UPDATE auth_login_challenges SET consumed_at=now() WHERE challenge_hash=%s '
        'AND consumed_at IS NULL AND expires_at>now() RETURNING user_id::text,owner_enrollment',
        (digest(token),),
    ).fetchone()
    return {'user_id': row[0], 'owner_enrollment': bool(row[1])} if row else None


def _bind_user(conn, user_id):
    conn.execute("SELECT set_config('app.user_id',%s,true)", (str(user_id),))


def enroll(conn, user_id):
    _bind_user(conn, user_id)
    secret = __import__('pyotp').random_base32()
    conn.execute(
        'INSERT INTO auth_mfa(user_id,encrypted_secret,key_version,confirmed_at,last_step) '
        'VALUES (%s,%s,1,NULL,-1) ON CONFLICT(user_id) DO UPDATE SET '
        'encrypted_secret=EXCLUDED.encrypted_secret,key_version=1,confirmed_at=NULL,last_step=-1',
        (str(user_id), seal(secret, MFA_LABEL)),
    )
    return secret


def confirmation_required(conn, user_id, email):
    row = conn.execute('SELECT confirmed_at FROM auth_mfa WHERE user_id=%s', (str(user_id),)).fetchone()
    owner = email.lower() == OWNER
    return owner or bool(row and row[0] is not None)


def confirm(conn, user_id, code):
    _bind_user(conn, user_id)
    row = conn.execute('SELECT encrypted_secret FROM auth_mfa WHERE user_id=%s AND confirmed_at IS NULL', (str(user_id),)).fetchone()
    if not row:
        return None
    try:
        secret = unseal(row[0], MFA_LABEL)
    except Exception:
        return None
    step = mfa.matching_step(secret, code)
    if step is None:
        return None
    done = conn.execute(
        'UPDATE auth_mfa SET confirmed_at=now(),last_step=%s WHERE user_id=%s AND confirmed_at IS NULL RETURNING user_id',
        (step, str(user_id)),
    ).fetchone()
    if not done:
        return None
    codes = [secrets.token_urlsafe(12) for _ in range(10)]
    conn.executemany(
        'INSERT INTO auth_recovery_codes(user_id,code_hash) VALUES (%s,%s)',
        [(str(user_id), digest(c)) for c in codes],
    )
    return codes


def verify(conn, user_id, code):
    _bind_user(conn, user_id)
    if isinstance(code, str) and len(code) == 6 and code.isdigit():
        row = conn.execute('SELECT encrypted_secret FROM auth_mfa WHERE user_id=%s AND confirmed_at IS NOT NULL', (str(user_id),)).fetchone()
        if not row:
            return False
        try:
            step = mfa.matching_step(unseal(row[0], MFA_LABEL), code)
        except Exception:
            return False
        return step is not None and mfa.consume(conn, str(user_id), step)
    if isinstance(code, str) and 12 <= len(code) <= 64:
        row = conn.execute(
            'UPDATE auth_recovery_codes SET used_at=now() WHERE user_id=%s AND code_hash=%s '
            'AND used_at IS NULL RETURNING user_id',
            (str(user_id), digest(code)),
        ).fetchone()
        return row is not None
    return False
