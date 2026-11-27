import asyncio,sys,os
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
 async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','gateway.mcp_preview'],env={'PATH':'/usr/bin:/bin','PYTHONPATH':os.getcwd()})) as (r,w):
  async with ClientSession(r,w) as s:
   await s.initialize(); tools=(await s.list_tools()).tools
   assert {t.name for t in tools}=={'marketing_readiness','marketing_demo_summary'}
   assert all(t.annotations.read_only_hint for t in tools)
   assert not (await s.call_tool('marketing_readiness',{})).is_error
   assert not (await s.call_tool('marketing_demo_summary',{'source':'ga4'})).is_error
   assert (await s.call_tool('marketing_demo_summary',{'source':'unknown'})).is_error
   print('MCP protocol: 5 checks passed (synthetic only)')
asyncio.run(main())
