"""Exercise the full local Google/MFA HTTP flow with signed offline identity assertions."""
import base64
from contextlib import contextmanager
import hashlib
import os
import secrets
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from authlib.jose import JsonWebKey, JsonWebToken
from fastapi.testclient import TestClient
import psycopg
import pyotp

import auth_service.app as auth

ORIGIN = 'https://fixture.invalid'
HEADERS = {'Origin': ORIGIN}
CLIENT_ID = 'offline-fixture'


def run():
    assert os.environ.get('PGHOST', '').startswith('/tmp/mi-pg.')
    assert os.environ.get('PGPORT') == '55489'
    assert os.environ.get('PGDATABASE') == 'postgres'
    with psycopg.connect() as conn:
        conn.execute('CREATE ROLE mi_google_fixture LOGIN NOINHERIT NOBYPASSRLS')
        conn.execute('GRANT marketing_auth TO mi_google_fixture')

    @contextmanager
    def fixture_db(role='marketing_auth'):
        assert role in {'marketing_auth', 'marketing_registration'}
        with psycopg.connect(user='mi_google_fixture') as conn:
            conn.execute('SET LOCAL ROLE ' + role)
            yield conn

    key = JsonWebKey.generate_key('RSA', 2048, is_private=True)
    jwt = JsonWebToken(['RS256'])
    expected_nonce = expected_verifier_challenge = None

    def signed_exchange(code, verifier, nonce, config, *, allow_owner):
        assert nonce == expected_nonce
        generated_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip('=')
        assert generated_challenge == expected_verifier_challenge
        now = int(time.time())
        claims = dict(iss='https://accounts.google.com', aud=CLIENT_ID,
                      sub='synthetic-' + code, email=code + '@example.invalid',
                      email_verified=True, nonce=nonce, iat=now, exp=now + 60)
        token = jwt.encode({'alg': 'RS256'}, claims, key).decode()
        return auth.google_verify.verify_token(
            token, key.as_dict(is_private=False), config['client_id'], nonce,
            allow_owner=allow_owner,
        )

    def google_login(client, code):
        nonlocal expected_nonce, expected_verifier_challenge
        start = client.get('/oauth/google/start')
        assert start.status_code == 303
        qs = parse_qs(urlsplit(start.headers['location']).query)
        expected_nonce, = qs['nonce']
        expected_verifier_challenge, = qs['code_challenge']
        assert qs['code_challenge_method'] == ['S256']
        assert client.cookies.get(auth.GOOGLE_STATE) == qs['state'][0]
        result = client.get('/oauth/google/callback', params={'state': qs['state'][0], 'code': code})
        assert result.status_code == 303
        assert auth.GOOGLE_STATE not in client.cookies
        return result

    env = {
        'MI_LOGIN_ENABLED': '1',
        'MI_REGISTRATION_ENABLED': '1',
        'MI_APP_ORIGIN': ORIGIN,
        'MI_GOOGLE_CLIENT_JSON': '/offline-placeholder',
        'MI_TOKEN_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    }
    with (patch.dict(os.environ, env), patch.object(auth, 'db', fixture_db),
          patch.object(auth.google_login, 'config', return_value={'client_id': CLIENT_ID}),
          patch.object(auth.google_verify, 'exchange', side_effect=signed_exchange),
          TestClient(auth.app, base_url=ORIGIN, follow_redirects=False) as client):
        assert 'data-google-enabled="1"' in client.get('/').text
        assert google_login(client, 'fixture').headers['location'] == '/'
        user_id = client.get('/v1/auth/me').json()['id']
        assert 'id="sign-out"' in client.get('/').text
        old_session = client.cookies.get(auth.COOKIE)
        assert client.post('/v1/auth/logout', headers=HEADERS).status_code == 200
        assert client.get('/v1/auth/me', headers={'Cookie': f'{auth.COOKIE}={old_session}'}).status_code == 401

        secret = pyotp.random_base32()
        with psycopg.connect() as conn:
            conn.execute(
                'INSERT INTO auth_mfa(user_id,encrypted_secret,key_version,confirmed_at) VALUES (%s,%s,1,now())',
                (user_id, auth.activation.seal(secret, auth.activation.MFA_LABEL)),
            )

        assert google_login(client, 'fixture').headers['location'] == '/?mfa=verify'
        assert auth.COOKIE not in client.cookies
        assert client.get('/v1/auth/me').status_code == 401
        assert 'id="mfa"' in client.get('/?mfa=verify').text
        challenge = client.cookies.get(auth.CHALLENGE)
        assert client.post('/v1/auth/mfa/enroll', headers=HEADERS).status_code == 403
        result = client.post('/v1/auth/mfa/verify', json={'code': pyotp.TOTP(secret).now()}, headers=HEADERS)
        assert result.status_code == 200
        assert auth.CHALLENGE not in client.cookies
        assert client.get('/v1/auth/me').json()['id'] == user_id
        assert client.post('/v1/auth/mfa/verify', json={'code': 'invalid'}, headers={
            **HEADERS, 'Cookie': f'{auth.CHALLENGE}={challenge}',
        }).status_code == 401
        assert client.post('/v1/auth/logout', headers=HEADERS).status_code == 200

        for _ in range(auth.MFA_ATTEMPT_LIMIT - 1):
            assert google_login(client, 'fixture').headers['location'] == '/?mfa=verify'
            assert client.post('/v1/auth/mfa/verify', json={'code': 'invalid'}, headers=HEADERS).status_code == 400
            assert auth.COOKIE not in client.cookies
        google_login(client, 'fixture')
        limited = client.post('/v1/auth/mfa/verify', json={'code': 'invalid'}, headers=HEADERS)
        assert limited.status_code == 429 and limited.headers['retry-after'] == '900'
        assert client.get('/v1/auth/me').status_code == 401
    print('PASS: Google callback, MFA gating, one-use challenge, account rate limit, and logout')


if __name__ == '__main__':
    run()
