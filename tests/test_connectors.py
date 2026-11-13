import os, sys
from urllib.parse import parse_qs, urlparse
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL','')
    monkeypatch.setenv('RZ_API_KEY','test-api-key')
    monkeypatch.setenv('RZ_MCP_ENCRYPTION_KEY','stable-test-key')
    monkeypatch.setenv('RZ_LOCAL_DATA_PATH',str(tmp_path/'connectors.sqlite3'))
    for name in ('app.main','app.connectors','app.plugin_manager','app.tools_hub','app.mcp_client'):
        sys.modules.pop(name,None)
    import app.main as module
    yield module
    sys.modules.pop('app.main',None)
    sys.modules.pop('app.connectors',None)

def test_storage_is_encrypted_and_owner_scoped(app):
    from app import connectors as c
    c._save('alice','google','sub-1','alice@example.com',{'access_token':'test-access','refresh_token':'test-refresh'})
    assert c._read('alice','google')['credentials']['access_token']=='test-access'
    with pytest.raises(KeyError): c._read('bob','google')
    conn,kind=c._db()
    try:
        raw=bytes(conn.execute('SELECT token_cipher FROM rz_app_connectors WHERE user_id=? AND provider=?',('alice','google')).fetchone()[0])
        assert b'test-access' not in raw and b'test-refresh' not in raw
    finally: conn.close()

def test_google_oauth_state_is_single_use_and_account_bound(app,monkeypatch):
    from fastapi.testclient import TestClient
    from app import connectors as c
    monkeypatch.setenv('GOOGLE_CLIENT_ID','client-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET','client-secret')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI','https://rz-ai.safenestt.com/v1/connectors/google/callback')
    app.app.dependency_overrides[c._user]=lambda:{'id':'alice','email':'alice@example.com'}
    client=TestClient(app.app)
    response=client.get('/v1/connectors/google/connect')
    assert response.status_code==200
    params=parse_qs(urlparse(response.json()['authorization_url']).query)
    assert params['access_type']==['offline']
    assert 'https://www.googleapis.com/auth/gmail.readonly' in params['scope'][0]
    state=params['state'][0]
    monkeypatch.setattr(c,'_oauth_exchange',lambda provider,code:{'credentials':{'access_token':'test-token'},'external_id':'sub-1','label':'alice@example.com'})
    callback=client.get('/v1/connectors/google/callback',params={'code':'single-use-code','state':state},follow_redirects=False)
    assert callback.status_code==303 and 'google_connected' in callback.headers['location']
    assert c._read('alice','google')['label']=='alice@example.com'
    replay=client.get('/v1/connectors/google/callback',params={'code':'again','state':state},follow_redirects=False)
    assert replay.status_code==400
    app.app.dependency_overrides.clear()

def test_stripe_restricted_key_validation_and_storage(app,monkeypatch):
    from fastapi.testclient import TestClient
    from app import connectors as c
    app.app.dependency_overrides[c._user]=lambda:{'id':'alice','email':'alice@example.com'}
    monkeypatch.setattr(c,'_http_json',lambda *args,**kwargs:{'id':'acct_123','email':'billing@example.com','business_profile':{'name':'Test Business'}})
    client=TestClient(app.app)
    assert client.post('/v1/connectors/stripe/api-key',json={'restricted_key':'not-a-restricted-key'}).status_code==422
    response=client.post('/v1/connectors/stripe/api-key',json={'restricted_key':'rk_test_'+'x'*32})
    assert response.status_code==200 and 'rk_test_' not in response.text
    assert c._read('alice','stripe')['credentials']['access_token'].startswith('rk_test_')
    app.app.dependency_overrides.clear()

def test_connected_account_only_gets_its_own_native_tools(app,monkeypatch):
    from app import connectors as c
    c._save('alice','github','42','@alice',{'access_token':'gh-test-token'})
    schemas=c.tool_schemas('alice')
    names={x['function']['name'] for x in schemas}
    assert 'appconn_list_connected_apps' in names
    assert 'appconn_github_list_repositories' in names
    assert all(name == 'appconn_list_connected_apps' or name.startswith('appconn_github_') for name in names)
    assert {x['function']['name'] for x in c.tool_schemas('bob')} == {'appconn_list_connected_apps'}
    calls=[]
    monkeypatch.setattr(c,'_get',lambda url,token,**kwargs:calls.append((url,token)) or [{'id':1,'name':'repo'}])
    result=c.execute_tool('alice','appconn_github_list_repositories',{'limit':5})
    assert result[0]['name']=='repo' and calls[0][1]=='gh-test-token'
    with pytest.raises(Exception): c.execute_tool('bob','appconn_github_list_repositories',{})

def test_native_connector_tool_runs_through_mia_agent_loop(app, monkeypatch):
    from app import connectors as c
    tool_name='appconn_gmail_search_messages'
    schema={'type':'function','function':{'name':tool_name,'description':'Search Gmail','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}}
    monkeypatch.setattr(c,'tool_schemas',lambda owner:[schema] if owner=='alice' else [])
    calls=[]
    monkeypatch.setattr(c,'execute_tool',lambda owner,name,args:calls.append((owner,name,args)) or {'messages':[{'id':'msg-1'}]})
    rounds=[]
    def completion(messages,model,temperature,max_tokens,tools=None,tool_choice='auto'):
        rounds.append(messages)
        if not any(m.get('role')=='tool' for m in messages):
            assert any(t['function']['name']==tool_name for t in tools)
            return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'gmail-call','type':'function','function':{'name':tool_name,'arguments':'{"query":"from:boss"}'}}]}}]}
        assert any(m.get('role')=='tool' and 'msg-1' in m.get('content','') for m in messages)
        return {'choices':[{'message':{'role':'assistant','content':'Found one email.'}}]}
    result=app._complete_with_mia_agent_tools('system','Search my Gmail','alice:connector-loop',None,None,None,completion_fn=completion)
    assert result['choices'][0]['message']['content']=='Found one email.'
    assert calls==[('alice',tool_name,{'query':'from:boss'})]
    assert len(rounds)==2


def test_connected_app_inventory_is_owner_scoped_and_never_returns_credentials(app):
    from app import connectors as c
    c._save('alice','google','sub-1','alice@example.com',{'access_token':'private-google-token'})
    c._save('alice','github','42','@alice',{'access_token':'private-github-token'})
    c._save('bob','stripe','acct-bob','Bob Stripe',{'access_token':'rk_test_private'})
    result=c.execute_tool('alice','appconn_list_connected_apps',{})
    assert result['count']==2
    assert {item['provider'] for item in result['integrations']}=={'google','github'}
    assert all(item['status']=='connected' for item in result['integrations'])
    serialized=str(result)
    assert 'private-google-token' not in serialized
    assert 'private-github-token' not in serialized
    assert 'rk_test_private' not in serialized
    assert c.execute_tool('bob','appconn_list_connected_apps',{})['count']==1


def test_broken_connector_does_not_hide_other_connector_tools(app,monkeypatch):
    from app import connectors as c
    c._save('alice','google','sub-1','alice@example.com',{'access_token':'google-token'})
    c._save('alice','github','42','@alice',{'access_token':'github-token'})
    original=c._read
    def selective_read(user_id,provider):
        if provider=='google':
            raise RuntimeError('simulated decrypt failure')
        return original(user_id,provider)
    monkeypatch.setattr(c,'_read',selective_read)
    names={x['function']['name'] for x in c.tool_schemas('alice')}
    assert 'appconn_list_connected_apps' in names
    assert 'appconn_github_list_repositories' in names
    assert 'appconn_gmail_search_messages' not in names


def test_gmail_search_returns_only_real_ids_and_sorts_by_gmail_internal_date(app, monkeypatch):
    from app import connectors as c
    c._save('alice','google','sub-1','alice@example.com',{'access_token':'gmail-token'})
    responses = {
        'messages': {
            'resultSizeEstimate': 3,
            'messages': [{'id':'18f-real-newest'},{'id':'17f-real-oldest'},{'id':'19f-real-middle'}],
        },
        '18f-real-newest': {
            'id':'18f-real-newest','threadId':'t-new','internalDate':'1794500000000',
            'payload':{'headers':[{'name':'From','value':'alerts@google.com'},{'name':'Subject','value':'Newest'}]},
        },
        '17f-real-oldest': {
            'id':'17f-real-oldest','threadId':'t-old','internalDate':'1794400000000',
            'payload':{'headers':[{'name':'From','value':'workspace.google.com'},{'name':'Subject','value':'Oldest'}]},
        },
        '19f-real-middle': {
            'id':'19f-real-middle','threadId':'t-mid','internalDate':'1794450000000',
            'payload':{'headers':[{'name':'From','value':'pay.google.com'},{'name':'Subject','value':'Middle'}]},
        },
    }
    calls=[]
    def fake_get(url, token, *, params=None, **kwargs):
        calls.append((url, params))
        if url.endswith('/messages'):
            return responses['messages']
        message_id=url.rsplit('/',1)[-1]
        return responses[message_id]
    monkeypatch.setattr(c,'_get',fake_get)
    result=c.execute_tool('alice','appconn_gmail_search_messages',{'query':'in:inbox from:google','limit':10})
    assert [m['id'] for m in result['messages']]==['18f-real-newest','19f-real-middle','17f-real-oldest']
    assert [m['subject'] for m in result['messages']]==['Newest','Middle','Oldest']
    assert result['sort']=='internalDate_desc_within_page'
    assert result['timestamps_verified_by']=='Gmail API messages.get'
    assert result['returnedCount']==3
    assert len(calls)==4
    assert all(m['id'] in {'18f-real-newest','17f-real-oldest','19f-real-middle'} for m in result['messages'])

def test_gmail_search_supports_pagination_and_reports_incomplete_scope(app, monkeypatch):
    from app import connectors as c
    c._save('alice','google','sub-1','alice@example.com',{'access_token':'gmail-token'})
    calls=[]
    def fake_get(url, token, *, params=None, **kwargs):
        calls.append((url, params))
        if url.endswith('/messages'):
            return {'resultSizeEstimate': 60, 'messages': [], 'nextPageToken': 'next-real-page-token'}
        raise AssertionError('No message metadata should be fetched for an empty page')
    monkeypatch.setattr(c,'_get',fake_get)
    result=c.execute_tool('alice','appconn_gmail_search_messages',{'query':'in:inbox','limit':50,'page_token':'previous-page-token'})
    assert calls[0][1]['pageToken']=='previous-page-token'
    assert calls[0][1]['maxResults']==50
    assert result['pageTokenUsed'] is True
    assert result['hasMoreResults'] is True
    assert result['nextPageToken']=='next-real-page-token'
    assert result['sort']=='internalDate_desc_within_page'


def test_gmail_get_message_returns_complete_available_body_and_attachment_metadata(app, monkeypatch):
    import base64
    from app import connectors as c
    c._save('alice','google','sub-1','alice@example.com',{'access_token':'gmail-token'})
    body='Full message body with the actual details.'
    encoded=base64.urlsafe_b64encode(body.encode()).decode().rstrip('=')
    payload={
        'id':'real-message-id','threadId':'real-thread-id','internalDate':'1794500000000',
        'snippet':'Actual Gmail snippet','labelIds':['INBOX'],
        'payload':{'mimeType':'multipart/mixed','headers':[
            {'name':'From','value':'sender@example.com'},{'name':'To','value':'alice@example.com'},
            {'name':'Cc','value':'copy@example.com'},{'name':'Subject','value':'Actual subject'},
            {'name':'Date','value':'Thu, 12 Nov 2026 12:00:00 -0500'}
        ],'parts':[
            {'mimeType':'text/plain','body':{'data':encoded}},
            {'mimeType':'application/pdf','filename':'invoice.pdf','body':{'attachmentId':'attachment-123','size':2048}}
        ]}
    }
    calls=[]
    monkeypatch.setattr(c,'_get',lambda url,token,**kwargs:calls.append(url) or payload)
    result=c.execute_tool('alice','appconn_gmail_get_message',{'message_id':'real-message-id'})
    assert result['id']=='real-message-id'
    assert result['subject']=='Actual subject'
    assert result['body']==body
    assert result['body_format']=='text/plain'
    assert result['body_truncated'] is False
    assert result['attachments'][0]['filename']=='invoice.pdf'
    assert result['attachments'][0]['content_retrieved'] is False
    assert result['source']=='Gmail API messages.get(format=full)'
    assert calls and calls[0].endswith('/real-message-id')

