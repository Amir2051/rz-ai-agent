import base64, json, os, time, unittest
from unittest.mock import patch

import pyotp
from auth_service import activation


KEY = base64.urlsafe_b64encode(b'0123456789abcdef0123456789abcdef').decode()


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []
        self.many = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        for key, value in self.rows.items():
            if key in sql:
                return Result(value)
        return Result(None)

    def executemany(self, sql, values):
        self.many.append((sql, list(values)))


class Activation(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'MI_TOKEN_ENCRYPTION_KEY': KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_seal_roundtrip_and_tamper(self):
        blob = activation.seal('synthetic-secret', activation.OIDC_LABEL)
        self.assertEqual(activation.unseal(blob, activation.OIDC_LABEL), 'synthetic-secret')
        with self.assertRaises(Exception):
            activation.unseal(blob[:-1] + bytes([blob[-1] ^ 1]), activation.OIDC_LABEL)

    def test_oidc_attempt_is_single_use(self):
        nonce = 'synthetic-nonce-value'
        verifier = 'synthetic-verifier-value'
        blob = activation.seal(json.dumps({'nonce': nonce, 'verifier': verifier}), activation.OIDC_LABEL)
        conn = FakeConn({'UPDATE auth_oidc_attempts': (activation.digest(nonce), blob)})
        got = activation.consume_oidc_attempt(conn, 'synthetic-state-value')
        self.assertEqual(got, activation.OidcAttempt(nonce, verifier, None))
        conn.rows = {}
        self.assertIsNone(activation.consume_oidc_attempt(conn, 'synthetic-state-value'))

    def test_oidc_attempt_wrong_nonce_rejected(self):
        blob = activation.seal(json.dumps({'nonce': 'other', 'verifier': 'v'}), activation.OIDC_LABEL)
        conn = FakeConn({'UPDATE auth_oidc_attempts': (activation.digest('expected'), blob)})
        self.assertIsNone(activation.consume_oidc_attempt(conn, 'synthetic-state-value'))

    def test_challenge_single_use(self):
        conn = FakeConn({'UPDATE auth_login_challenges': ('user-1', False)})
        self.assertEqual(activation.consume_challenge(conn, 'synthetic-challenge-token')['user_id'], 'user-1')
        conn.rows = {}
        self.assertIsNone(activation.consume_challenge(conn, 'synthetic-challenge-token'))

    def test_enroll_and_confirm_returns_recovery_codes_once(self):
        conn = FakeConn()
        secret = activation.enroll(conn, 'user-1')
        self.assertEqual(len(secret), 32)
        conn.rows = {'SELECT encrypted_secret': (activation.seal(secret, activation.MFA_LABEL),),
                     'UPDATE auth_mfa': ('user-1',)}
        step = int(time.time()) // 30
        codes = activation.confirm(conn, 'user-1', pyotp.TOTP(secret).at(step * 30))
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(conn.many[0][1]), 10)
        conn.rows = {'SELECT encrypted_secret': (activation.seal(secret, activation.MFA_LABEL),)}
        self.assertIsNone(activation.confirm(conn, 'user-1', '000000'))

    def test_verify_recovery_code_single_use(self):
        code = 'synthetic-recovery-code'
        conn = FakeConn({'UPDATE auth_recovery_codes': ('user-1',)})
        self.assertTrue(activation.verify(conn, 'user-1', code))
        conn.rows = {}
        self.assertFalse(activation.verify(conn, 'user-1', code))

    def test_owner_requires_mfa_even_without_enrollment(self):
        conn = FakeConn()
        self.assertTrue(activation.confirmation_required(conn, 'user-1', activation.OWNER))
        self.assertFalse(activation.confirmation_required(conn, 'user-1', 'customer@example.invalid'))


if __name__ == '__main__':
    unittest.main()
