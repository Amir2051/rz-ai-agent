import psycopg
from marketing.ai_budget import reserve
W='30000000-0000-4000-8000-000000000001';A='10000000-0000-4000-8000-000000000001'
with psycopg.connect('') as c:
 c.execute('GRANT marketing_ai_credentials TO mi_fixture_service')
 c.execute('INSERT INTO ai_daily_budget(workspace_id,day,limit_microusd) VALUES (%s,(now() AT TIME ZONE \'UTC\')::date,100)',(W,))
def attempt(n):
 with psycopg.connect('',user='mi_fixture_service') as c:
  c.execute('SET LOCAL ROLE marketing_ai_credentials');c.execute("SELECT set_config('app.user_id',%s,true)",(A,));return reserve(c,W,n)
from concurrent.futures import ThreadPoolExecutor
def try_reserve(_):
 try:return attempt(60)
 except PermissionError:return None
with ThreadPoolExecutor(2) as pool:r=list(pool.map(try_reserve,range(2)))
assert sorted(x for x in r if x is not None)==[60],r
assert attempt(40)==100
try:attempt(1)
except PermissionError:pass
else:raise AssertionError('Overspend')
print('PASS: concurrent budget reservations, persistence, exact cap, overspend denial')
