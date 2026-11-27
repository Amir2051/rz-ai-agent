"""Deterministic, offline marketing planning. No provider calls or external writes."""
from dataclasses import dataclass
from datetime import date

CHANNELS = frozenset({'facebook','instagram','google_ads','meta_ads','seo','email','youtube'})
OBJECTIVES = frozenset({'awareness','leads','traffic','retention'})
@dataclass(frozen=True)
class Campaign:
    workspace: str
    title: str
    channel: str
    objective: str
    start: date
    end: date
    budget_cents: int = 0
    def __post_init__(self):
        if not self.workspace or not self.title.strip() or len(self.title)>120: raise ValueError('Invalid campaign identity')
        if self.channel not in CHANNELS or self.objective not in OBJECTIVES: raise ValueError('Unsupported channel or objective')
        if self.end < self.start: raise ValueError('Invalid dates')
        if type(self.budget_cents) is not int or self.budget_cents != 0: raise PermissionError('Paid activity disabled')

def plan(campaign: Campaign, allowed_workspaces: frozenset[str]) -> dict:
    if campaign.workspace not in allowed_workspaces: raise PermissionError('Workspace not authorized')
    tasks={'awareness':['Draft educational content','Review brand and claims','Prepare organic distribution plan'], 'leads':['Define audience','Draft opt-in landing page','Review consent requirements'], 'traffic':['Audit landing page','Draft search-friendly content','Prepare measurement plan'], 'retention':['Review customer communication preferences','Draft helpful update','Review opt-out requirements']}
    return {'title':campaign.title,'channel':campaign.channel,'objective':campaign.objective,'tasks':tasks[campaign.objective],'budget_cents':0,'status':'draft','external_execution':False,'metrics':None,'requires_review':True}
