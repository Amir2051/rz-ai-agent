from marketing.storage import bind

def append(conn,user,workspace,client,tool,outcome):
    conn.execute('SET LOCAL ROLE marketing_mcp_audit')
    conn.execute("SELECT set_config('app.user_id',%s,true)",(user,))
    conn.execute('INSERT INTO mcp_activity(workspace_id,actor_id,client_id,tool,outcome) VALUES (%s,%s,%s,%s,%s)',(workspace,user,client,tool,outcome))
def history(conn,user,workspace):
    bind(conn,user)
    rows=conn.execute('SELECT client_id,tool,outcome,created_at FROM mcp_activity WHERE workspace_id=%s ORDER BY created_at DESC,id LIMIT 100',(workspace,)).fetchall()
    return [{'client':r[0],'tool':r[1],'outcome':r[2],'created_at':r[3].isoformat()} for r in rows]
