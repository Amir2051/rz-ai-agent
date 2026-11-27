"""Fail-closed policy for already cryptographically verified OIDC claims."""
import time,hmac
OWNER='yayamoussa2050@gmail.com'
def validate_claims(claims,client_id,nonce,now=None,allow_owner=False):
    now=time.time() if now is None else now
    if claims.get('iss') not in ('https://accounts.google.com','accounts.google.com'):raise ValueError('Issuer rejected')
    if claims.get('aud')!=client_id or claims.get('azp',client_id)!=client_id:raise ValueError('Audience rejected')
    if not isinstance(claims.get('exp'),(int,float)) or claims['exp']<=now:raise ValueError('Expired identity')
    if not isinstance(claims.get('iat'),(int,float)) or not now-600<=claims['iat']<=now+30:raise ValueError('Stale identity')
    if not isinstance(claims.get('nonce'),str) or not hmac.compare_digest(claims['nonce'],nonce):raise ValueError('Nonce rejected')
    email=claims.get('email');subject=claims.get('sub')
    if claims.get('email_verified') is not True or not isinstance(email,str) or not 3<=len(email)<=254 or '@' not in email or not isinstance(subject,str) or not 1<=len(subject)<=255:raise ValueError('Identity rejected')
    email=email.lower()
    if email==OWNER and not allow_owner:raise ValueError('Administrator requires separate enrollment')
    return subject,email
