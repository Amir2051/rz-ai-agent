"""Local-only browser fixture server, with real API and disposable PostgreSQL."""
import os,threading,subprocess
from contextlib import contextmanager
from unittest.mock import patch
import psycopg,uvicorn
import auth_service.app as a
assert os.environ.get('PGHOST','').startswith('/tmp/mi-pg.') and os.environ.get('PGPORT')=='55489'
@contextmanager
def db():
 with psycopg.connect('',user='mi_fixture_service') as c:
  c.execute('SET LOCAL ROLE marketing_auth');yield c
# Browser routes HTTPS fixture requests through local HTTP test server, preserving origin.
with patch.object(a,'db',db),patch.dict(os.environ,{'MI_APP_ORIGIN':'https://fixture.invalid'}):
 import socket
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(a.app,log_level='warning'))
 t=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);t.start()
 import time
 for _ in range(100):
  if server.started:break
  time.sleep(.05)
 assert server.started
 try:subprocess.run(['node','scripts/test_dashboard_integrated.cjs',str(port)],check=True)
 finally:server.should_exit=True;t.join(5)
