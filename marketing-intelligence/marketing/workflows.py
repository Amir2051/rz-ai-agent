"""Offline CRM, director and automation policy. No external execution."""
from dataclasses import dataclass
from datetime import datetime
@dataclass(frozen=True)
class Lead:
    workspace: str
    reference: str
    consent: bool
    stage: str = 'new'
def lead_preview(lead, allowed):
    if not lead.workspace or lead.workspace not in allowed: raise PermissionError('Workspace denied')
    if not lead.reference.strip() or lead.stage not in {'new','qualified','contacted','closed'}: raise ValueError('Invalid lead')
    return {'reference':lead.reference,'stage':lead.stage,'consent_recorded':lead.consent is True,'outreach_enabled':False}
def director(workspace, objective, allowed):
    if not workspace or workspace not in allowed: raise PermissionError('Workspace denied')
    if objective not in {'awareness','leads','traffic','retention'}: raise ValueError('Unknown objective')
    return {'objective':objective,'steps':['Review verified analytics','Prepare campaign draft','Request human review'],'mode':'deterministic_preview','model_called':False,'execution_enabled':False}
def automation(workspace, kind, due, allowed):
    if not workspace or workspace not in allowed: raise PermissionError('Workspace denied')
    if kind not in {'report','review','draft'} or not isinstance(due,datetime) or due.utcoffset() is None: raise ValueError('Invalid automation')
    return {'kind':kind,'due':due.isoformat(),'status':'draft','scheduled':False}
def authorize_action(workspace, action, cost_cents, allowed):
    if not workspace or workspace not in allowed or action not in {'preview','draft','report'} or type(cost_cents) is not int or cost_cents != 0: raise PermissionError('Action denied')
    return True
