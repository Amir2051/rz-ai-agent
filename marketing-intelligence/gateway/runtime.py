"""Database-backed read-only MCP runtime with audience binding and full audit."""
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider, AccessToken
from fastmcp.server.dependencies import get_access_token
from mcp.types import ToolAnnotations

from gateway.grants import resolve
from gateway.oauth_server import resource_url, issuer_url
from marketing.mcp_activity import append, history
from marketing.storage import list_drafts
from anyio import to_thread


def build(db):
    def lookup(token):
        with db() as conn:
            return resolve(conn, token)

    class Auth(AuthProvider):
        async def verify_token(self, token):
            r = await to_thread.run_sync(lookup, token)
            if not r:
                return None
            user, workspace, client, expiry, audience, scopes = r
            if audience != resource_url() or 'marketing:read' not in scopes:
                return None
            return AccessToken(token=token, client_id=client, subject=user,
                               scopes=list(scopes), expires_at=expiry,
                               resource=resource_url(),
                               claims={'workspace': workspace, 'audience': audience, 'iss': resource_url().rsplit('/mcp', 1)[0]})

    s = FastMCP('SafeNestT Marketing Intelligence',
                auth=Auth(base_url=issuer_url(), resource_base_url=resource_url(),
                          required_scopes=['marketing:read']),
                mask_error_details=True, strict_input_validation=True)

    def execute(name, fn):
        token = get_access_token()
        if not token:
            raise PermissionError('Authentication required')
        with db() as c:
            r = resolve(c, token.token)
            if not r:
                raise PermissionError('Grant unavailable')
            user, workspace, client, _expiry, audience, scopes = r
            if audience != resource_url() or 'marketing:read' not in scopes:
                append(c, user, workspace, client, name, 'denied')
                raise PermissionError('Token audience or scope rejected')
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (workspace,))
            daily = c.execute(
                "SELECT count(*) FROM mcp_activity WHERE workspace_id=%s "
                "AND created_at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
                (workspace,),
            ).fetchone()[0]
            client_calls = c.execute(
                "SELECT count(*) FROM mcp_activity WHERE workspace_id=%s AND client_id=%s "
                "AND created_at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
                (workspace, client),
            ).fetchone()[0]
            if daily >= 500 or client_calls >= 200:
                append(c, user, workspace, client, name, 'denied')
                raise PermissionError('Daily limit reached')
            try:
                result = fn(c, user, workspace)
            except Exception:
                append(c, user, workspace, client, name, 'failed')
                raise
            append(c, user, workspace, client, name, 'success')
        return result

    @s.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def get_campaigns():
        return execute('get_campaigns', lambda c, u, w: {
            'drafts': [x for x in list_drafts(c, u, w) if x['kind'] == 'campaign'],
            'live_verified': False,
        })

    @s.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def get_activity():
        return execute('get_activity', lambda c, u, w: {'activity': history(c, u, w)})

    @s.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_connections():
        return execute('list_connections', lambda c, u, w: {'connections': [], 'live_verified': False})

    return s
