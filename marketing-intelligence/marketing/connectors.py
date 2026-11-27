"""Offline provider parser integration. No authentication or network execution."""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from hub.connectors.catalog import connector_class
from hub.core.models import UnifiedRow, CORE_METRICS

SOURCES=frozenset({'ga4','gsc','meta_ads','google_ads','youtube'})
@dataclass(frozen=True)
class Account:
    workspace: str
    source: str
    account_id: str

def catalog():
    return {s: {'reports':sorted(connector_class(s).get_reports()),'live_enabled':False} for s in sorted(SOURCES)}

def normalize(account: Account, allowed: frozenset[Account], rows: list[dict], start: date, end: date):
    if account not in allowed or account.source not in SOURCES or not account.workspace or not account.account_id: raise PermissionError('Account not authorized')
    if end < start or (end-start).days>90 or len(rows)>10000: raise ValueError('Invalid query bounds')
    result=[]
    for raw in rows:
        if raw.get('account_id')!=account.account_id or raw.get('source',account.source)!=account.source or raw.get('report','core')!='core': raise PermissionError('Unexpected row scope')
        row=UnifiedRow.model_validate({**raw,'source':account.source,'report':'core'})
        if not start<=row.date<=end: raise ValueError('Unexpected date')
        for metric in CORE_METRICS:
            v=getattr(row,metric)
            if v is not None and (not isfinite(v) or v<0): raise ValueError('Invalid metric')
        result.append(row.model_dump(mode='json',exclude={'extras'}))
    return {'workspace':account.workspace,'source':account.source,'account_id':account.account_id,'mode':'offline_fixture','rows':result,'live_verified':False}
