"""Provider response adapters; offline only until verified account transport exists."""
from hub.connectors.ga4 import parse_ga4_report
from hub.connectors.gsc import parse_gsc_response
from hub.connectors.meta_ads import parse_meta_insights
from hub.connectors.google_ads import parse_google_ads_rows
from hub.connectors.youtube import parse_youtube_response
from marketing.connectors import normalize,Account
PARSERS={'ga4':parse_ga4_report,'gsc':parse_gsc_response,'meta_ads':parse_meta_insights,'google_ads':parse_google_ads_rows,'youtube':parse_youtube_response}
def adapt_fixture(account:Account,allowed:frozenset[Account],response,start,end):
    if account not in allowed or account.source not in PARSERS: raise PermissionError('Account denied')
    if end<start or (end-start).days>90: raise ValueError('Invalid date window')
    if not isinstance(response,(dict,list)): raise ValueError('Invalid response')
    raw=response if isinstance(response,list) else response.get('rows',[])
    if not isinstance(raw,list) or len(raw)>10000: raise ValueError('Invalid response size')
    rows=PARSERS[account.source](response,account.account_id)
    if account.source=='meta_ads':
        for original,row in zip(raw,rows):
            if 'actions' not in original: row['conversions']=None
    return normalize(account,allowed,rows,start,end)
