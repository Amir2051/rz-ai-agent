import os, socket, sys, tempfile
with tempfile.TemporaryDirectory(prefix='mi-test-') as home:
 os.environ.clear(); os.environ.update(HOME=home, XDG_CONFIG_HOME=home, XDG_DATA_HOME=home, PATH='/usr/bin:/bin', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
 def blocked(*a, **k): raise RuntimeError('External network disabled for offline tests')
 socket.socket.connect=blocked; socket.socket.connect_ex=blocked; socket.create_connection=blocked
 import pytest
 raise SystemExit(pytest.main(sys.argv[1:]))
