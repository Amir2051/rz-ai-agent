from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:Literal['openai','anthropic','openrouter','gemini','ollama']
    model:str=Field(min_length=1,max_length=200,pattern=r'^\S+$')
    max_output:int=Field(default=512,ge=1,le=2048,strict=True)
def bind(conn,user):
    conn.execute('SET LOCAL ROLE marketing_ai_credentials')
    conn.execute("SELECT set_config('app.user_id',%s,true)",(user,))
def get(conn,user,workspace):
    bind(conn,user)
    r=conn.execute('SELECT provider,model,max_output FROM ai_settings WHERE workspace_id=%s',(workspace,)).fetchone()
    return {'configured':bool(r),'provider':r[0] if r else None,'model':r[1] if r else None,'max_output':r[2] if r else None,'live_enabled':False}
def save(conn,user,workspace,payload):
    bind(conn,user)
    conn.execute('INSERT INTO ai_settings(workspace_id,provider,model,max_output) VALUES (%s,%s,%s,%s) ON CONFLICT(workspace_id) DO UPDATE SET provider=excluded.provider,model=excluded.model,max_output=excluded.max_output,updated_at=now()',(workspace,payload.provider,payload.model,payload.max_output))
    return {'configured':True,'live_enabled':False}
