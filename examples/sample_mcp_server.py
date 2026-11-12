"""Minimal local stdio MCP server for testing the RZ-AI plugin bridge."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP("RZ Sample Plugin")

@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
def echo(message: str) -> str:
    """Return the supplied message so the RZ plugin bridge can be tested safely."""
    return f"MCP echo: {message}"

@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
def current_status() -> dict:
    """Return a harmless sample status object."""
    return {"status": "ok", "service": "rz-sample-mcp"}

if __name__ == "__main__":
    server.run(transport="stdio")
