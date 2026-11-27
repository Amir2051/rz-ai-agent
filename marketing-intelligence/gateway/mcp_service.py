"""Private MCP protocol foundation. No live data or external actions."""
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from fastmcp.server.auth import AuthProvider,AccessToken
from fastmcp.server.dependencies import get_access_token
from datetime import datetime,timezone
import hashlib,hmac

def build_server(token_digest,workspace):
    if not token_digest or len(token_digest)!=64 or not workspace:raise ValueError('Explicit fixture authorization required')
    class FixtureAuth(AuthProvider):
        async def verify_token(self,token):
            if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),token_digest):return None
            return AccessToken(token=token,client_id='fixture-client',scopes=['marketing:read'],expires_at=int(datetime.now(timezone.utc).timestamp())+60)
    server=FastMCP('SafeNestT Marketing Intelligence',auth=FixtureAuth(required_scopes=['marketing:read']),mask_error_details=True,strict_input_validation=True)
    def scope():
        t=get_access_token()
        if t is None or 'marketing:read' not in t.scopes:raise PermissionError('Not authorized')
        return workspace
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_connections()->dict:
        return {'workspace':scope(),'connections':[],'mode':'offline_fixture','live_verified':False}
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def get_metrics()->dict:
        return {'workspace':scope(),'metrics':None,'reason':'No verified connected data','live_verified':False}
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def get_usage()->dict:
        return {'workspace':scope(),'usage':None,'external_execution':False}
    return server
