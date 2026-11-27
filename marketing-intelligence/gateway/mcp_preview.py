"""Local stdio MCP, synthetic only. No upstream credentials or network calls."""
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from gateway.policy import Scope, execute
server=FastMCP('SafeNestT Marketing Preview')
@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def marketing_readiness() -> dict:
    return {'mode':'synthetic','live_accounts_connected':False,'external_writes_enabled':False,'agent_runtime_connected':False}
@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def marketing_demo_summary(source: str) -> dict:
    scope=Scope('demo','account1')
    return execute('synthetic.'+source+'.summary',scope,frozenset({scope}))
if __name__=='__main__': server.run(transport='stdio')
