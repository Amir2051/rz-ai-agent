"""Google OIDC authorization preparation; activation intentionally disabled."""
import json,os,stat,secrets,hashlib,base64
from pathlib import Path
from urllib.parse import urlencode
CALLBACK='https://marketing.safenestt.com/oauth/google/callback'
def config(path):
    p=Path(path)
    if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)!=0o600 or p.stat().st_uid!=os.getuid():raise ValueError('Unsafe credential file')
    d=json.loads(p.read_text())['web']
    if CALLBACK not in d.get('redirect_uris',[]) or not d.get('client_id') or not d.get('client_secret'):raise ValueError('Invalid Google configuration')
    return d

def prepare(client_id):
    state,nonce,verifier=(secrets.token_urlsafe(32) for _ in range(3))
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    url='https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(dict(client_id=client_id,redirect_uri=CALLBACK,response_type='code',scope='openid email profile',state=state,nonce=nonce,code_challenge=challenge,code_challenge_method='S256'))
    return dict(url=url,state=state,nonce=nonce,verifier=verifier)
