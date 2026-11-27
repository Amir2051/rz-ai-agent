"""TOTP verification primitives; enrollment and session issuance are separate."""
import hmac,re,time
import pyotp

def matching_step(secret,code,now=None):
    if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):return None
    step=int(time.time() if now is None else now)//30
    totp=pyotp.TOTP(secret)
    for candidate in (step,step-1,step+1):
        if candidate>=0 and hmac.compare_digest(totp.at(candidate*30),code):return candidate
    return None

def consume(conn,user_id,step):
    if not isinstance(step,int) or step<0:return False
    return conn.execute('UPDATE auth_mfa SET last_step=%s WHERE user_id=%s AND confirmed_at IS NOT NULL AND last_step<%s RETURNING user_id',(step,user_id,step)).fetchone() is not None
