import hashlib,secrets,psycopg
from contextlib import contextmanager
from starlette.testclient import TestClient
from gateway.runtime import build
A='10000000-0000-4000-8000-000000000001';W='30000000-0000-4000-8000-000000000001'
token=secrets.token_urlsafe(32); refresh=secrets.token_urlsafe(32)
bad_token=secrets.token_urlsafe(32)
resource='https://marketing.safenestt.com/mcp'
with psycopg.connect('') as c:
    c.execute("INSERT INTO oauth_clients(client_id,client_secret_hash,name,redirect_uris) VALUES ('fixture-client',%s,'synthetic',ARRAY['http://127.0.0.1:8090/callback']) ON CONFLICT DO NOTHING",('a'*64,))
    c.execute("INSERT INTO oauth_tokens(token_hash,refresh_hash,client_id,user_id,workspace_id,audience,scopes,family_id,expires_at,refresh_expires_at) VALUES (%s,%s,'fixture-client',%s,%s,%s,ARRAY['marketing:read'],gen_random_uuid(),now()+interval '1 hour',now()+interval '1 day')",(hashlib.sha256(token.encode()).hexdigest(),hashlib.sha256(refresh.encode()).hexdigest(),A,W,resource))
    c.execute("INSERT INTO oauth_tokens(token_hash,refresh_hash,client_id,user_id,workspace_id,audience,scopes,family_id,expires_at,refresh_expires_at) VALUES (%s,%s,'fixture-client',%s,%s,%s,ARRAY['marketing:read'],gen_random_uuid(),now()+interval '1 hour',now()+interval '1 day')",(hashlib.sha256(bad_token.encode()).hexdigest(),hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),A,W,'https://wrong.example/mcp'))

@contextmanager
def db():
    with psycopg.connect('',user='mi_fixture_service') as c:
        c.execute('SET LOCAL ROLE marketing_auth');yield c

with TestClient(build(db).http_app(path='/mcp',stateless_http=True,json_response=True)) as c:
    h={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'}
    def call(name):return c.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{}}})
    r=call('get_campaigns');assert r.status_code==200 and not r.json()['result'].get('isError'),r.text
    with psycopg.connect('') as p:
        assert p.execute("SELECT count(*) FROM mcp_activity WHERE tool='get_campaigns'").fetchone()[0]==1
        p.execute('UPDATE oauth_tokens SET revoked_at=now()')
    assert call('get_campaigns').status_code==401
    wrong=dict(h);wrong['Authorization']='Bearer '+bad_token
    assert c.post('/mcp',headers=wrong,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'get_campaigns','arguments':{}}}).status_code==401
print('PASS: audience-bound OAuth grant, wrong-audience rejection, scoped tool, durable audit, revocation')
