"""Single-account, offline reporting; no invented totals or cross-source sums."""
from decimal import Decimal
from datetime import date
from math import isfinite
METRICS=('impressions','clicks','sessions','conversions')
def summarize(batch: dict, workspace: str, source: str, account_id: str) -> dict:
    if not all((workspace,source,account_id)) or (batch.get('workspace'),batch.get('source'),batch.get('account_id'))!=(workspace,source,account_id): raise PermissionError('Scope mismatch')
    if batch.get('mode')!='offline_fixture' or batch.get('live_verified') is not False: raise ValueError('Only offline fixtures supported')
    rows=batch['rows']; seen=set()
    for r in rows:
        if (r.get('source'),r.get('account_id'),r.get('report'))!=(source,account_id,'core'): raise PermissionError('Row scope mismatch')
        key=(date.fromisoformat(r['date']),r.get('campaign_id'),r.get('campaign'))
        if key in seen: raise ValueError('Duplicate report grain')
        seen.add(key)
    totals={}
    for m in METRICS:
        values=[r.get(m) for r in rows]
        if any(v is not None and (type(v) not in (int,float) or not isfinite(v) or v<0) for v in values): raise ValueError('Invalid metric')
        totals[m]=float(sum((Decimal(str(v)) for v in values),Decimal(0))) if values and all(v is not None for v in values) else None
    i,c=totals['impressions'],totals['clicks']
    return {'mode':'offline_fixture','live_verified':False,'source':source,'account_id':account_id,'row_count':len(rows),'metrics':totals,'ctr_percent':100*c/i if i and c is not None else None,'spend':None,'revenue':None,'coverage':'unverified','note':'Fixture row totals only; currency and completeness not verified.'}
