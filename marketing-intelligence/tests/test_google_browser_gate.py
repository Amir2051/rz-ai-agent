from unittest.mock import patch
from fastapi.testclient import TestClient
from auth_service.app import app

def test_google_disabled():
    with patch.dict('os.environ', {'MI_LOGIN_ENABLED':'0','MI_REGISTRATION_ENABLED':'0'}):
        c=TestClient(app,base_url='https://marketing.safenestt.com')
        assert c.get('/oauth/google/start').status_code==503
        assert c.get('/oauth/google/callback?state=x&code=y').status_code==503

def test_google_browser_state_required():
    with patch.dict('os.environ', {'MI_LOGIN_ENABLED':'1','MI_REGISTRATION_ENABLED':'1'}):
        c=TestClient(app,base_url='https://marketing.safenestt.com')
        assert c.get('/oauth/google/callback?state=x&code=y').status_code==400
        c.cookies.set('__Host-mi_google_state','wrong')
        assert c.get('/oauth/google/callback?state=x&code=y').status_code==400
