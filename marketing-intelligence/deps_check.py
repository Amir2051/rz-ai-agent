import importlib.util
for name in ('fastapi', 'uvicorn', 'psycopg', 'argon2', 'fastmcp', 'mcp', 'authlib', 'pyotp', 'cryptography', 'httpx', 'pytest', 'playwright'):
    print(name, bool(importlib.util.find_spec(name)))
