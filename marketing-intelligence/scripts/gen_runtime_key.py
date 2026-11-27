"""Create a 0600 runtime encryption key file if absent. Never prints the key."""
import base64, os, pathlib, secrets
p = pathlib.Path('secrets/marketing-runtime.env')
if not p.exists():
    value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    p.write_text('MI_TOKEN_ENCRYPTION_KEY=' + value + '\n')
    os.chmod(p, 0o600)
os.chmod(p, 0o600)
print('runtime key file ready (mode 0600)')
