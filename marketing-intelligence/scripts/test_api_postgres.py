import os
from contextlib import contextmanager
from unittest.mock import patch
import psycopg
from fastapi.testclient import TestClient
import auth_service.app as a
A='10000000-0000-4000-8000-000000000001'; B='10000000-0000-4000-8000-000000000002'; W='30000000-0000-4000-8000-000000000001'; X='30000000-0000-4000-8000-000000000002'
with psycopg.connect('') as c:
 c.execute("CREATE ROLE mi_fixture_service LOGIN NOINHERIT NOBYPASSRLS")
 c.execute('GRANT marketing_auth TO mi_fixture_service')
 for u,w,email,role in [(A,W,'a@example.invalid','owner'),(B,X,'b@example.invalid','viewer')]:
  org=c.execute('INSERT INTO organizations(name) VALUES (%s) RETURNING id',(email,)).fetchone()[0]
  c.execute('INSERT INTO auth_users(id,email,password_hash) VALUES (%s,%s,%s)',(u,email,'fixture'))
  c.execute('INSERT INTO organization_members VALUES (%s,%s,%s)',(org,u,role))
  c.execute('INSERT INTO workspaces(id,organization_id,name) VALUES (%s,%s,%s)',(w,org,email))
  c.execute("INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES (%s,%s,now()+interval '1 hour')",(u,a.digest('fixture-'+u)))
@contextmanager
def fixture_db():
 with psycopg.connect('',user='mi_fixture_service') as c:
  c.execute('SET LOCAL ROLE marketing_auth');yield c
with patch.object(a,'db',fixture_db),patch.dict(os.environ,{'MI_APP_ORIGIN':'https://fixture.invalid'}):
 client=TestClient(a.app,base_url='https://fixture.invalid');client.cookies.set(a.COOKIE,'fixture-'+A)
 assert client.get('/v1/auth/me').json()['id']==A
 assert [r['id'] for r in client.get('/v1/workspaces').json()]==[W]
 payload={'kind':'campaign','title':'Synthetic campaign','start':'2026-12-01','end':'2026-12-02'}
 path=f'/v1/workspaces/{W}/drafts'; headers={'Origin':'https://fixture.invalid'}
 r=client.post(path,json=payload,headers=headers);assert r.status_code==201,r.text
 assert client.get(path).json()[0]['id']==r.json()['id']
 assert client.post(path,json=payload).status_code==403
 assert client.post(f'/v1/workspaces/{X}/drafts',json=payload,headers=headers).status_code==404
 client.cookies.set(a.COOKIE,'fixture-'+B)
 assert client.get(path).json()==[]
 assert client.post(f'/v1/workspaces/{X}/drafts',json=payload,headers=headers).status_code==404
 client.cookies.clear();assert client.get(path).status_code==401
 print('PASS: 8 authenticated API persistence and isolation assertions')
with patch.object(a,'db',fixture_db),patch.dict(os.environ,{'MI_APP_ORIGIN':'https://fixture.invalid'}):
 client=TestClient(a.app,base_url='https://fixture.invalid');client.cookies.set(a.COOKIE,'fixture-'+A)
 path=f'/v1/workspaces/{W}/ai-settings';payload={'provider':'openrouter','model':'fixture-model','max_output':128};headers={'Origin':'https://fixture.invalid'}
 assert client.put(path,json=payload,headers=headers).status_code==200
 assert client.get(path).json()['model']=='fixture-model'
 assert client.put(path,json={**payload,'live_enabled':True},headers=headers).status_code==422
 assert client.put(path,json=payload).status_code==403
 client.cookies.set(a.COOKIE,'fixture-'+B)
 assert not client.get(path).json()['configured']
 assert client.put(path,json=payload,headers=headers).status_code==404
 assert client.put(f'/v1/workspaces/{X}/ai-settings',json=payload,headers=headers).status_code==404
 print('PASS: 7 AI settings persistence and authorization assertions')
from marketing.mcp_activity import append
with fixture_db() as conn:append(conn,A,W,'fixture-client','get_metrics','success')
with patch.object(a,'db',fixture_db):
 client=TestClient(a.app,base_url='https://fixture.invalid');client.cookies.set(a.COOKIE,'fixture-'+A)
 path=f'/v1/workspaces/{W}/activity';assert client.get(path).json()[0]['tool']=='get_metrics'
 client.cookies.set(a.COOKIE,'fixture-'+B);assert client.get(path).json()==[]
 client.cookies.clear();assert client.get(path).status_code==401
print('PASS: durable activity history, tenant isolation and authentication')
