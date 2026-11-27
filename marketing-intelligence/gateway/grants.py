"""MCP grant verification.

Access tokens are audience-bound OAuth 2.1 tokens. Direct table reads are
revoked from the verifier role; a definer function handles legacy grants.
"""
import hashlib

from marketing.storage import bind


def verify_oauth_token(conn, token):
    if not isinstance(token, str) or not 32 <= len(token) <= 512:
        return None
    conn.execute('SET LOCAL ROLE marketing_mcp_verify')
    row = conn.execute(
        'SELECT token_hash,user_id::text,workspace_id::text,client_id,audience,scopes,'
        'extract(epoch from expires_at)::bigint FROM oauth_tokens '
        'WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>now()',
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    if not row:
        return None
    conn.execute('SET LOCAL ROLE marketing_reader')
    conn.execute("SELECT set_config('app.user_id',%s,true)", (row[1],))
    visible = conn.execute('SELECT 1 FROM workspaces WHERE id=%s', (row[2],)).fetchone()
    if not visible:
        return None
    return {'user': row[1], 'workspace': row[2], 'client': row[3],
            'audience': row[4], 'scopes': list(row[5]), 'expires_at': row[6]}


def resolve(conn, token):
    """Return (user_id, workspace_id, client_id, expiry, audience, scopes)."""
    r = verify_oauth_token(conn, token)
    if not r:
        return None
    return (r['user'], r['workspace'], r['client'], r['expires_at'], r['audience'], r['scopes'])


def revoke(conn, token_hash):
    conn.execute('SET LOCAL ROLE marketing_mcp_verify')
    conn.execute('UPDATE oauth_tokens SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL', (token_hash,))
