"""Transactional provisioning for verified Google identities; not exposed as an HTTP endpoint."""
from auth_service.identity_policy import OWNER
from uuid import uuid4

def provision(conn,subject,email,password_hash):
    if email!=email.lower() or email==OWNER or not subject:raise ValueError('Identity not eligible')
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('google:'+subject,))
    existing=conn.execute("SELECT u.id::text,u.email,u.disabled_at FROM auth_external_identities i JOIN auth_users u ON u.id=i.user_id WHERE i.provider='google' AND i.subject=%s",(subject,)).fetchone()
    if existing:
        if existing[1]!=email or existing[2] is not None:raise ValueError('Identity not eligible')
        return existing[0]
    if conn.execute('SELECT 1 FROM auth_users WHERE email=%s',(email,)).fetchone():raise ValueError('Explicit account linking required')
    user,org,workspace=(str(uuid4()) for _ in range(3))
    conn.execute("SELECT set_config('app.registration_org',%s,true),set_config('app.registration_user',%s,true)",(org,user))
    conn.execute('INSERT INTO auth_users(id,email,password_hash) VALUES (%s,%s,%s)',(user,email,password_hash))
    conn.execute("INSERT INTO organizations(id,name) VALUES (%s,'My organization')",(org,))
    conn.execute("INSERT INTO organization_members(organization_id,user_id,role) VALUES (%s,%s,'owner')",(org,user))
    conn.execute("INSERT INTO workspaces(id,organization_id,name) VALUES (%s,%s,'My workspace')",(workspace,org))
    conn.execute("INSERT INTO auth_external_identities(provider,subject,user_id) VALUES ('google',%s,%s)",(subject,user))
    return user

def provision_password(conn,email,password_hash):
    """Create a password-only tenant owner. No external identity is asserted."""
    if email!=email.lower() or email==OWNER or not password_hash:raise ValueError('Identity not eligible')
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('password:'+email,))
    if conn.execute('SELECT 1 FROM auth_users WHERE email=%s',(email,)).fetchone():raise ValueError('Account already exists')
    user,org,workspace=(str(uuid4()) for _ in range(3))
    conn.execute("SELECT set_config('app.registration_org',%s,true),set_config('app.registration_user',%s,true)",(org,user))
    conn.execute('INSERT INTO auth_users(id,email,password_hash) VALUES (%s,%s,%s)',(user,email,password_hash))
    conn.execute("INSERT INTO organizations(id,name) VALUES (%s,'My organization')",(org,))
    conn.execute("INSERT INTO organization_members(organization_id,user_id,role) VALUES (%s,%s,'owner')",(org,user))
    conn.execute("INSERT INTO workspaces(id,organization_id,name) VALUES (%s,%s,'My workspace')",(workspace,org))
    return user
