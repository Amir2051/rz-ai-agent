import os,psycopg
from auth_service.mfa import consume
assert os.environ.get('PGHOST','').startswith('/tmp/mi-pg.') and os.environ.get('PGPORT')=='55489'
with psycopg.connect() as c:
 u=c.execute("INSERT INTO auth_users(email,password_hash) VALUES ('mfa@example.invalid','!fixture') RETURNING id").fetchone()[0]
 c.execute('INSERT INTO auth_mfa(user_id,encrypted_secret,key_version) VALUES (%s,%s,1)',(u,b'x'*32))
 assert not consume(c,u,100)
 c.execute('UPDATE auth_mfa SET confirmed_at=now() WHERE user_id=%s',(u,))
 assert consume(c,u,100) and not consume(c,u,100) and not consume(c,u,99) and consume(c,u,101)
 c.rollback()
print('PASS: unconfirmed MFA denied, replay and older step denied, newer step accepted')
