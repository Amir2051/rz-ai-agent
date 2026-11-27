import os, unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway import oauth_server


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        for key, value in self.rows.items():
            if key in sql:
                return Result(value)
        return Result(None)


@contextmanager
def fake_db():
    yield FakeConn()


class OAuthServer(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'MI_APP_ORIGIN': 'https://marketing.safenestt.com',
            'MI_MCP_RESOURCE': 'https://marketing.safenestt.com/mcp',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = FastAPI()
        oauth_server.install(self.app, fake_db)
        self.client = TestClient(self.app, base_url='https://marketing.safenestt.com')

    def test_redirect_validation(self):
        self.assertTrue(oauth_server._valid_redirect('https://chatgpt.com/callback'))
        self.assertTrue(oauth_server._valid_redirect('http://127.0.0.1:8090/callback'))
        self.assertFalse(oauth_server._valid_redirect('http://example.com/callback'))
        self.assertFalse(oauth_server._valid_redirect('https://chatgpt.com/cb#fragment'))

    def test_protected_resource_metadata(self):
        body = self.client.get('/.well-known/oauth-protected-resource/mcp').json()
        self.assertEqual(body['resource'], 'https://marketing.safenestt.com/mcp')
        self.assertEqual(body['authorization_servers'], ['https://marketing.safenestt.com'])
        self.assertIn('marketing:read', body['scopes_supported'])

    def test_authorization_server_metadata(self):
        body = self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(body['issuer'], 'https://marketing.safenestt.com')
        self.assertEqual(body['code_challenge_methods_supported'], ['S256'])
        self.assertIn('refresh_token', body['grant_types_supported'])
        self.assertTrue(body['authorization_response_iss_parameter_supported'])

    def test_client_registration(self):
        response = self.client.post('/oauth/register', json={
            'client_name': 'synthetic', 'redirect_uris': ['http://127.0.0.1:8090/callback']})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(len(body['client_id']), 32)
        self.assertEqual(len(body['client_secret']), 43)
        self.assertEqual(body['token_endpoint_auth_method'], 'client_secret_post')

    def test_client_registration_rejects_unsafe_redirect(self):
        response = self.client.post('/oauth/register', json={
            'client_name': 'synthetic', 'redirect_uris': ['http://example.com/callback']})
        self.assertEqual(response.status_code, 400)

    def test_token_requires_valid_client(self):
        response = self.client.post('/oauth/token', data={
            'grant_type': 'authorization_code', 'client_id': 'missing',
            'client_secret': 'synthetic', 'code': 'x', 'redirect_uri': 'http://127.0.0.1:8090/callback'})
        self.assertEqual(response.status_code, 401)

    def test_authorize_requires_pkce_s256(self):
        with patch('auth_service.app.current_user', return_value={'id': 'user-1', 'email': 'customer@example.invalid'}):
            response = self.client.get('/oauth/authorize', params={
                'response_type': 'code', 'client_id': 'missing',
                'redirect_uri': 'http://127.0.0.1:8090/callback',
                'state': 'synthetic-state-value', 'scope': 'marketing:read'})
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
