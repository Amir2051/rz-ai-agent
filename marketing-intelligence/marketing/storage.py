"""Append-only draft persistence. Caller supplies authenticated user, never client identity."""
from uuid import UUID
from psycopg.types.json import Jsonb
KINDS=frozenset({'campaign','content','lead','automation'})
def bind(conn,user_id,write=False):
    uid=str(UUID(user_id))
    conn.execute('SET LOCAL ROLE marketing_drafter' if write else 'SET LOCAL ROLE marketing_reader')
    conn.execute("SELECT set_config('app.user_id',%s,true)",(uid,))
    return uid
def save(conn,user_id,workspace,kind,payload):
    if kind not in KINDS or not isinstance(payload,dict): raise ValueError('Invalid draft')
    if any(payload.get(k) is not False for k in ('external_execution',)): raise ValueError('Execution must be disabled')
    uid=bind(conn,user_id,True)
    row=conn.execute('INSERT INTO marketing_drafts(workspace_id,actor_id,kind,payload) VALUES (%s,%s,%s,%s) RETURNING id::text',(str(UUID(workspace)),uid,kind,Jsonb(payload))).fetchone()
    return {'id':row[0],'status':'draft'}
def list_drafts(conn,user_id,workspace):
    bind(conn,user_id)
    rows=conn.execute('SELECT id::text,kind,payload,created_at FROM marketing_drafts WHERE workspace_id=%s ORDER BY created_at DESC,id LIMIT 100',(str(UUID(workspace)),)).fetchall()
    return [{'id':r[0],'kind':r[1],'payload':r[2],'created_at':r[3].isoformat()} for r in rows]
