import os,psycopg
from auth_service.customer_registration import provision
assert os.environ.get('PGHOST','').startswith('/tmp/mi-pg.') and os.environ.get('PGPORT')=='55489'
with psycopg.connect() as c:
 c.execute('SET LOCAL ROLE marketing_registration')
 a=provision(c,'fixture-a','a@example.invalid','!disabled-password')
 b=provision(c,'fixture-b','b@example.invalid','!disabled-password')
 assert a!=b and provision(c,'fixture-a','a@example.invalid','!disabled-password')==a
 for subject,email in [('fixture-c','a@example.invalid'),('fixture-a','b@example.invalid'),('fixture-owner','yayamoussa2050@gmail.com')]:
  try:provision(c,subject,email,'!disabled-password')
  except ValueError:pass
  else:raise AssertionError('Identity collision accepted')
 c.execute('RESET ROLE')
 for u in (a,b):
  c.execute('SET LOCAL ROLE marketing_reader');c.execute("SELECT set_config('app.user_id',%s,true)",(u,));assert c.execute('SELECT count(*) FROM workspaces').fetchone()[0]==1;c.execute('RESET ROLE')
 c.rollback()
print('PASS: isolated provisioning, idempotence, identity collision rejection')
