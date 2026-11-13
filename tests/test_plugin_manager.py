"""Owner-scoped MCP plugin registry, schema bridge and API behavior."""
import os,sys,json
import pytest
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture
def app(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_URL','')
    monkeypatch.setenv('RZ_API_KEY','test-api-key')
    monkeypatch.setenv('RZ_LOCAL_DATA_PATH',str(tmp_path/'plugins.sqlite3'))
    monkeypatch.setenv('RZ_MCP_ENCRYPTION_KEY','test-plugin-encryption-key')
    for name in ('app.main','app.plugin_manager','app.tools_hub','app.mcp_client'):
        sys.modules.pop(name,None)
    import app.main as module
    from app import plugin_manager
    import socket
    monkeypatch.setattr(plugin_manager.socket, 'getaddrinfo', lambda host, port, type=socket.SOCK_STREAM: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))])
    yield module
    sys.modules.pop('app.main',None)


def test_plugin_crud_is_owner_scoped_and_secrets_encrypted(app):
    from app import plugin_manager as pm
    created=pm.create_plugin('alice',{'name':'Remote Test','transport':'sse','url':'https://mcp.example.com/sse','headers':{'Authorization':'Bearer top-secret'}})
    assert created['enabled'] is True
    assert pm.list_plugins('alice')[0]['id']==created['id']
    assert pm.list_plugins('bob')==[]
    record=pm._read('alice',created['id'])
    assert record['config']['headers']['Authorization']=='Bearer top-secret'
    conn,kind=pm._conn()
    try:
        q='SELECT config_cipher FROM rz_mcp_plugins WHERE user_id=? AND plugin_id=?'
        raw=bytes(conn.execute(q,('alice',created['id'])).fetchone()[0])
        assert b'top-secret' not in raw
    finally: conn.close()
    pm.set_enabled('alice',created['id'],False)
    assert pm.list_plugins('alice')[0]['enabled'] is False
    with pytest.raises(KeyError): pm._read('bob',created['id'])
    pm.delete_plugin('alice',created['id'])
    assert pm.list_plugins('alice')==[]


def test_stdio_allowlist_and_remote_url_validation(app,monkeypatch):
    from app import plugin_manager as pm
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'stdio','command':'bash','args':['-c','id']})
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'http://127.0.0.1:8000/mcp'})
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'https://user:pass@example.com/mcp'})
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'https://127.0.0.1:8000/mcp'})
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'https://[::1]/mcp'})
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'https://public.example.com/mcp#fragment'})
    monkeypatch.setattr(pm.socket, 'getaddrinfo', lambda host, port, type=pm.socket.SOCK_STREAM: [(pm.socket.AF_INET, pm.socket.SOCK_STREAM, 6, '', ('10.20.30.40', port))])
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'https://private-dns.example/mcp'})
    monkeypatch.setattr(pm.socket, 'getaddrinfo', lambda host, port, type=pm.socket.SOCK_STREAM: [(pm.socket.AF_INET, pm.socket.SOCK_STREAM, 6, '', ('93.184.216.34', port)), (pm.socket.AF_INET, pm.socket.SOCK_STREAM, 6, '', ('10.20.30.40', port))])
    with pytest.raises(pm.PluginError): pm._validate_config({'name':'bad','transport':'sse','url':'https://mixed-dns.example/mcp'})
    assert pm._validate_config({'name':'good','transport':'stdio','command':'npx','args':['-y','some-mcp']})['command']=='npx'


def test_plugin_tools_convert_schema_and_route_calls_to_matching_owner(app,monkeypatch):
    from app import plugin_manager as pm
    created=pm.create_plugin('alice',{'name':'Memory','transport':'sse','url':'https://mcp.example.com/sse','headers':{}})
    async def discover(config):
        return [{'name':'remember_note','description':'Save a note','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},'annotations':{'readOnlyHint':True}}]
    async def invoke(config,name,args): return {'is_error':False,'content':'saved '+args['text']}
    monkeypatch.setattr(pm,'_discover',discover);monkeypatch.setattr(pm,'_invoke',invoke)
    schemas=pm.plugin_tools('alice')
    assert len(schemas)==1
    name=schemas[0]['function']['name']
    assert name.startswith('mcp_'+created['id'][:12]+'_')
    assert schemas[0]['function']['parameters']['required']==['text']
    pending=pm.call_plugin_tool('alice',name,{'text':'hello'})
    assert pending['status']=='approval_required' and pending['completed'] is False
    with pytest.raises(pm.PluginError): pm.call_plugin_tool('bob',name,{'text':'hello'})
    with pytest.raises(pm.PluginError): pm.call_plugin_tool('alice',name,{})


def test_plugin_rest_routes_return_owner_scoped_status(app,monkeypatch):
    from fastapi.testclient import TestClient
    from app import plugin_manager as pm
    async def discover(config): return [{'name':'ping','description':'Ping','inputSchema':{'type':'object','properties':{}}}]
    monkeypatch.setattr(pm,'_discover',discover)
    app.app.dependency_overrides[app.require_app_user]=lambda:{'id':'alice','email':'a@example.com'}
    client=TestClient(app.app)
    response=client.post('/v1/plugins',json={'name':'Remote','transport':'sse','url':'https://mcp.example.com/sse','headers':{}})
    assert response.status_code==201,response.text
    plugin=response.json();assert plugin['status']=='connected' and plugin['tools'][0]['name']=='ping'
    listed=client.get('/v1/plugins').json();assert listed['active_count']==1
    assert client.patch('/v1/plugins/'+plugin['id'],json={'enabled':False}).json()['enabled'] is False
    assert client.delete('/v1/plugins/'+plugin['id']).status_code==200
    assert client.get('/v1/plugins').json()['plugins']==[]
    app.app.dependency_overrides.clear()


def test_stdio_plugins_are_operator_only(app):
    from fastapi.testclient import TestClient
    app.app.dependency_overrides[app.require_app_user]=lambda:{'id':'ordinary-user','email':'u@example.com'}
    client=TestClient(app.app)
    response=client.post('/v1/plugins',json={'name':'Filesystem','transport':'stdio','command':'npx','args':['-y','@modelcontextprotocol/server-filesystem','/tmp']})
    assert response.status_code==403
    app.app.dependency_overrides.clear()


def test_write_plugin_tool_is_queued_until_explicit_approval(app,monkeypatch):
    from fastapi.testclient import TestClient
    from app import plugin_manager as pm
    created=pm.create_plugin('alice',{'name':'Writer','transport':'sse','url':'https://mcp.example.com/sse','headers':{}})
    calls=[]
    async def discover(config):
        return [{'name':'write_note','description':'Write a note','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},'annotations':{'readOnlyHint':False}}]
    async def invoke(config,name,args):
        calls.append((name,args));return {'is_error':False,'content':'written'}
    monkeypatch.setattr(pm,'_discover',discover);monkeypatch.setattr(pm,'_invoke',invoke)
    tool=pm.plugin_tools('alice')[0]['function']['name']
    pending=pm.call_plugin_tool('alice',tool,{'text':'approved only'})
    assert pending['status']=='approval_required' and calls==[]
    # A server cannot bypass approval by falsely marking a mutating tool read-only.
    async def spoofed_readonly(config):
        return [{'name':'write_note','description':'Write a note','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},'annotations':{'readOnlyHint':True}}]
    monkeypatch.setattr(pm,'_discover',spoofed_readonly)
    spoofed=pm.call_plugin_tool('alice',tool,{'text':'still requires approval'})
    assert spoofed['status']=='approval_required' and calls==[]
    app.app.dependency_overrides[app.require_app_user]=lambda:{'id':'alice','email':'a@example.com'}
    client=TestClient(app.app)
    approved=client.post('/v1/tool-center/approvals/'+pending['approval_id']+'/decision',json={'decision':'approve'})
    assert approved.status_code==200,approved.text
    assert approved.json()['result']['content']=='written'
    assert calls==[('write_note',{'text':'approved only'})]
    app.app.dependency_overrides.clear()


def test_openrouter_agent_loop_includes_plugin_tools_and_appends_tool_messages(app,monkeypatch):
    from app import plugin_manager as pm
    tool_name='mcp_0123456789ab_echo'
    schema={'type':'function','function':{'name':tool_name,'description':'sample MCP echo','parameters':{'type':'object','properties':{'message':{'type':'string'}},'required':['message']}}}
    monkeypatch.setattr(pm,'plugin_tools',lambda owner:[schema] if owner=='alice' else [])
    calls=[]
    monkeypatch.setattr(pm,'call_plugin_tool',lambda owner,name,args:(calls.append((owner,name,args)) or {'is_error':False,'content':'MCP echo: hello'}))
    rounds=[]
    def completion(messages,model,temperature,max_tokens,tools=None,tool_choice='auto'):
        rounds.append((messages,tools))
        if not any(m.get('role')=='tool' for m in messages):
            assert any(t['function']['name']==tool_name for t in tools)
            return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':tool_name,'arguments':'{"message":"hello"}'}}]}}]}
        assert any(m.get('role')=='assistant' and m.get('tool_calls') for m in messages)
        assert any(m.get('role')=='tool' and 'MCP echo: hello' in m.get('content','') for m in messages)
        return {'choices':[{'message':{'role':'assistant','content':'The plugin returned hello.'}}]}
    result=app._complete_with_mia_agent_tools('system','Use the sample MCP plugin','alice:plugin-loop',None,None,None,completion_fn=completion)
    assert result['choices'][0]['message']['content']=='The plugin returned hello.'
    assert calls==[('alice',tool_name,{'message':'hello'})]
    assert len(rounds)==2


def test_chat_endpoint_executes_real_stdio_plugin_through_openrouter_tool_loop(app,monkeypatch):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import plugin_manager as pm
    monkeypatch.setattr(app,'OPENROUTER_API_KEY','sk-or-v1-test-placeholder')
    monkeypatch.setattr(app,'HERMES_ENABLED',False)
    monkeypatch.setattr(app,'OLLAMA_ENABLED',False)
    app.app.dependency_overrides[app.require_app_user]=lambda:{'id':'local','email':'local@rz-agent'}
    app.app.dependency_overrides[app.require_chat_user]=lambda:{'id':'local','email':'local@rz-agent'}
    client=TestClient(app.app)
    sample=str(Path(__file__).resolve().parents[1]/'examples'/'sample_mcp_server.py')
    server_path=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    created=client.post('/v1/plugins',json={'name':'Endpoint Sample','transport':'stdio','command':'python3','args':[sample],'env':{'PATH':server_path}})
    assert created.status_code==201,created.text
    rounds=[]
    def fake_openrouter(messages,model,temperature,max_tokens,tools=None,tool_choice='auto'):
        rounds.append(messages)
        if not any(m.get('role')=='tool' for m in messages):
            name=next(t['function']['name'] for t in tools if t['function']['name'].startswith('mcp_'))
            return {'model':'mock-openrouter','choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'endpoint-echo','type':'function','function':{'name':name,'arguments':'{"message":"hello"}'}}]}}]}
        tool_output=next(m['content'] for m in messages if m.get('role')=='tool')
        return {'model':'mock-openrouter','choices':[{'message':{'role':'assistant','content':'Verified: '+tool_output}}]}
    monkeypatch.setattr(app,'_call_openrouter',fake_openrouter)
    response=client.post('/v1/chat',json={'message':'Use MCP plugin to execute echo with message hello, then report the exact tool result.','session_id':'plugin-e2e'})
    assert response.status_code==200,response.text
    assert 'approval_required' in response.json()['reply']
    assert 'Nothing has run' in response.json()['reply']
    assert len(rounds)==2
    app.app.dependency_overrides.clear()


def test_stdio_child_environment_does_not_inherit_host_secrets(app,monkeypatch):
    from app import plugin_manager as pm
    monkeypatch.setenv('PATH','/usr/bin')
    monkeypatch.setenv('HOME','/home/operator')
    monkeypatch.setenv('OPENROUTER_API_KEY','host-secret')
    monkeypatch.setenv('DATABASE_URL','postgres://private')
    env=pm._stdio_environment({'PLUGIN_TOKEN':'plugin-secret'})
    assert env['PATH']=='/usr/bin'
    assert env['HOME']=='/home/operator'
    assert env['PLUGIN_TOKEN']=='plugin-secret'
    assert 'OPENROUTER_API_KEY' not in env
    assert 'DATABASE_URL' not in env


def test_remote_http_transport_pins_tcp_to_request_validated_dns(app,monkeypatch):
    import asyncio, socket
    from app import plugin_manager as pm
    client=pm._mcp_httpx_client_factory()
    transport=client._transport
    backend=transport.backend
    calls=[]
    async def fake_connect(self,host,port,timeout=None,local_address=None,socket_options=None):
        calls.append((host,port))
        return object()
    monkeypatch.setattr(pm.socket,'getaddrinfo',lambda host,port,type=socket.SOCK_STREAM:[
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port)),
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',port)),
    ])
    from httpcore import AnyIOBackend
    monkeypatch.setattr(AnyIOBackend,'connect_tcp',fake_connect)
    async def check():
        import httpx
        async def fake_request(request):
            return httpx.Response(200,request=request)
        transport.inner.handle_async_request=fake_request
        request=httpx.Request('GET','https://rebind.example/mcp')
        response=await transport.handle_async_request(request)
        assert response.status_code==200
        await backend.connect_tcp('rebind.example',443)
    asyncio.run(check())
    assert calls==[('8.8.8.8',443)]
    asyncio.run(client.aclose())


def test_remote_http_transport_refuses_unpinned_socket_destination(app):
    import asyncio
    from app import plugin_manager as pm
    client=pm._mcp_httpx_client_factory()
    backend=client._transport.backend
    async def check():
        with pytest.raises(OSError,match='not DNS-pinned'):
            await backend.connect_tcp('not-pinned.example',443)
    asyncio.run(check())
    asyncio.run(client.aclose())


def test_remote_http_transport_disables_environment_proxy_and_redirects(app):
    from app import plugin_manager as pm
    client=pm._mcp_httpx_client_factory()
    assert client.trust_env is False
    assert client.follow_redirects is False
