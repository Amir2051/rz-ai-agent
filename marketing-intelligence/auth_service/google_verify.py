"""Verify Google ID token signatures against Google's fixed JWKS endpoint."""
import httpx
from authlib.jose import JsonWebToken
from auth_service.identity_policy import validate_claims
JWKS='https://www.googleapis.com/oauth2/v3/certs'
TOKEN='https://oauth2.googleapis.com/token'
def verify_token(token,keys,client_id,nonce):
    if not isinstance(token,str) or len(token)>16384:raise ValueError('Invalid identity token')
    claims=JsonWebToken(['RS256']).decode(token,keys)
    claims.validate(leeway=0)
    return validate_claims(claims,client_id,nonce)
def exchange(code,verifier,nonce,config):
    from auth_service.google_login import CALLBACK
    with httpx.Client(timeout=10,follow_redirects=False) as client:
        response=client.post(TOKEN,data=dict(code=code,code_verifier=verifier,client_id=config['client_id'],client_secret=config['client_secret'],redirect_uri=CALLBACK,grant_type='authorization_code'))
        response.raise_for_status()
        token=response.json()['id_token']
        keys=client.get(JWKS);keys.raise_for_status()
        return verify_token(token,keys.json(),config['client_id'],nonce)
