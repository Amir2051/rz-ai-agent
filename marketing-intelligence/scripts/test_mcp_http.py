import hashlib,secrets
from starlette.testclient import TestClient
from gateway.mcp_service import build_server
secret=secrets.token_urlsafe(32)
s=build_server(hashlib.sha256(secret.encode()).hexdigest(),'fixture-workspace')
app=s.http_app(path='/mcp',stateless_http=True,json_response=True)
headers={'Authorization':'Bearer '+secret,'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
def rpc(c,method,params=None,h=headers):
 return c.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
with TestClient(app) as c:
 assert rpc(c,'tools/list',h={**headers,'Authorization':'Bearer invalid'}).status_code==401
 r=rpc(c,'tools/list');assert r.status_code==200,r.text
 assert {x['name'] for x in r.json()['result']['tools']}=={'list_connections','get_metrics','get_usage'}
 r=rpc(c,'tools/call',{'name':'get_metrics','arguments':{}});assert r.status_code==200,r.text
 assert 'fixture-workspace' in r.text and 'live_verified' in r.text
 r=rpc(c,'tools/call',{'name':'publish','arguments':{}});assert r.json().get('error') or r.json()['result']['isError']
print('PASS: authenticated HTTP MCP discovery, tool invocation, invalid token and unknown tool denial')
