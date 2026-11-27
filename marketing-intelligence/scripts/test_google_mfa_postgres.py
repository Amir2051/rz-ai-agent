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
                      sub='synthetic-' + code,
                      email=auth.activation.OWNER if code in {'owner', 'unverified'} else code + '@example.invalid',
                      email_verified=code != 'unverified', nonce=nonce, iat=now, exp=now + 60)
        token = jwt.encode({'alg': 'RS256'}, claims, key).decode()
        return auth.google_verify.verify_token(
            token, key.as_dict(is_private=False), config['client_id'], nonce,
            allow_owner=allow_owner,
        )

    def google_callback(client, url, code):
        nonlocal expected_nonce, expected_verifier_challenge
        qs = parse_qs(urlsplit(url).query)
        expected_nonce, = qs['nonce']
        expected_verifier_challenge, = qs['code_challenge']
        assert qs['code_challenge_method'] == ['S256']
        assert client.cookies.get(auth.GOOGLE_STATE) == qs['state'][0]
        return client.get('/oauth/google/callback', params={'state': qs['state'][0], 'code': code})

    def google_login(client, code):
        start = client.get('/oauth/google/start')
        assert start.status_code == 303
        result = google_callback(client, start.headers['location'], code)
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
        assert client.post('/v1/auth/mfa/enroll', headers=HEADERS).status_code == 401
        credentials = {'email': 'password-fixture@example.invalid', 'password': 'synthetic-password-registration'}
        assert client.post('/v1/auth/register', json={**credentials, 'email': auth.activation.OWNER},
                           headers=HEADERS).status_code == 409
        assert client.post('/v1/auth/register', json=credentials, headers=HEADERS).status_code == 201
        assert client.post('/v1/auth/login', json=credentials, headers=HEADERS).status_code == 200
        assert client.get('/v1/auth/me').json()['email'] == credentials['email']
        assert client.post('/v1/auth/logout', headers=HEADERS).status_code == 200

        expired_start = client.get('/oauth/google/start')
        state = client.cookies.get(auth.GOOGLE_STATE)
        with psycopg.connect() as conn:
            conn.execute("UPDATE auth_oidc_attempts SET expires_at=now()-interval '1 second' WHERE state_hash=%s",
                         (auth.digest(state),))
        assert google_callback(client, expired_start.headers['location'], 'fixture').status_code == 400
        assert auth.COOKIE not in client.cookies
        assert google_login(client, 'fixture').headers['location'] == '/'
        user_id = client.get('/v1/auth/me').json()['id']
        assert 'id="sign-out"' in client.get('/').text
        assert 'id="google-link" hidden' in client.get('/').text
        sample = {'password': 'fixture-password', 'code': '000000'}
        assert client.post('/v1/auth/google/link', json=sample, headers=HEADERS).status_code == 403
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
        with psycopg.connect() as conn:
            conn.execute("UPDATE auth_login_challenges SET expires_at=now()-interval '1 second' WHERE challenge_hash=%s",
                         (auth.digest(challenge),))
        assert client.post('/v1/auth/mfa/verify', json={'code': pyotp.TOTP(secret).now()},
                           headers=HEADERS).status_code == 401
        assert client.get('/v1/auth/me').status_code == 401

        assert google_login(client, 'fixture').headers['location'] == '/?mfa=verify'
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
        assert client.post('/v1/auth/google/link', json=sample, headers=HEADERS).status_code == 401

        owner_secret = pyotp.random_base32()
        password = 'synthetic-account-password'
        with psycopg.connect() as conn:
            owner_id, = conn.execute(
                'INSERT INTO auth_users(email,password_hash) VALUES (%s,%s) RETURNING id',
                (auth.activation.OWNER, auth.hasher.hash(password)),
            ).fetchone()
            conn.execute(
                'INSERT INTO auth_mfa(user_id,encrypted_secret,key_version,confirmed_at) VALUES (%s,%s,1,now())',
                (owner_id, auth.activation.seal(owner_secret, auth.activation.MFA_LABEL)),
            )

        # An enrollment challenge issued before confirmation cannot replace a
        # confirmed factor. Also exercise the persistence guard directly.
        with fixture_db() as conn:
            stale_enrollment = auth.activation.create_challenge(conn, owner_id, owner_enrollment=True)
        with psycopg.connect() as conn:
            before = conn.execute(
                'SELECT encrypted_secret,confirmed_at,last_step FROM auth_mfa WHERE user_id=%s',
                (owner_id,),
            ).fetchone()
        assert client.post('/v1/auth/mfa/enroll', headers={
            **HEADERS, 'Cookie': f'{auth.CHALLENGE}={stale_enrollment}',
        }).status_code == 403
        with fixture_db() as conn:
            auth.activation.enroll(conn, owner_id)
            after = conn.execute(
                'SELECT encrypted_secret,confirmed_at,last_step FROM auth_mfa WHERE user_id=%s',
                (owner_id,),
            ).fetchone()
            assert after == before

        start = client.get('/oauth/google/start')
        assert google_callback(client, start.headers['location'], 'owner').status_code == 400
        assert auth.COOKIE not in client.cookies

        assert client.post('/v1/auth/login', json={
            'email': auth.activation.OWNER, 'password': password,
        }, headers=HEADERS).json()['mfa_required']
        step_time = int(time.time()) // 30 * 30
        totp = pyotp.TOTP(owner_secret)
        assert client.post('/v1/auth/mfa/verify', json={'code': totp.at(step_time - 30)},
                           headers=HEADERS).status_code == 200
        assert 'id="google-link" hidden' not in client.get('/').text
        mismatch = auth.google_login.prepare(CLIENT_ID)
        with fixture_db() as conn:
            auth.activation.create_oidc_attempt(
                conn, mismatch['state'], mismatch['nonce'], mismatch['verifier'],
                owner_session_hash=auth.digest('different-session'),
            )
        client.cookies.delete(auth.GOOGLE_STATE)
        client.cookies.set(auth.GOOGLE_STATE, mismatch['state'])
        assert google_callback(client, mismatch['url'], 'owner').status_code == 401
        client.cookies.delete(auth.GOOGLE_STATE)

        link_path = '/v1/auth/google/link'
        assert client.post(link_path, json=sample, headers=HEADERS).status_code == 401
        assert client.post(link_path, json={'password': password, 'code': totp.at(step_time - 30)},
                           headers=HEADERS).status_code == 401
        proof = {'password': password, 'code': totp.at(step_time)}
        assert client.post(link_path, json=proof).status_code == 403
        link = client.post(link_path, json=proof, headers=HEADERS)
        assert link.status_code == 200
        assert google_callback(client, link.json()['authorization_url'], 'unverified').status_code == 400
        with psycopg.connect() as conn:
            assert conn.execute('SELECT 1 FROM auth_external_identities WHERE user_id=%s',
                                (owner_id,)).fetchone() is None
        link = client.post(link_path, json={'password': password, 'code': totp.at(step_time + 30)},
                           headers=HEADERS)
        assert link.status_code == 200
        url = link.json()['authorization_url']
        link_state = client.cookies.get(auth.GOOGLE_STATE)
        session = client.cookies.get(auth.COOKIE)
        result = google_callback(client, url, 'owner')
        assert result.status_code == 303
        assert result.headers['location'] == '/'
        assert 'id="google-linked" hidden' not in client.get('/').text
        with psycopg.connect() as conn:
            assert conn.execute("SELECT subject FROM auth_external_identities WHERE user_id=%s",
                                (owner_id,)).fetchone() == ('synthetic-owner',)
        assert client.cookies.get(auth.COOKIE) == session
        client.cookies.set(auth.GOOGLE_STATE, link_state)
        assert google_callback(client, url, 'owner').status_code == 400
        client.cookies.delete(auth.GOOGLE_STATE)
        assert client.post('/v1/auth/logout', headers=HEADERS).status_code == 200
        assert google_login(client, 'owner').headers['location'] == '/?mfa=verify'
        assert client.get('/v1/auth/me').status_code == 401
    print('PASS: Google callback, MFA gating, one-use challenge, account rate limit, and logout')
    print('PASS: password registration and login work with the owner registration restriction')
    print('PASS: missing enrollment proof, confirmed factor replacement, and expired OAuth/MFA state rejected')
    print('PASS: owner linking requires password and fresh MFA, preserves the session, and uses the verified subject')



if __name__ == '__main__':
    run()
