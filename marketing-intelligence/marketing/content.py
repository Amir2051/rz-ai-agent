"""Local content draft validation; no scheduling or publishing side effects."""
from dataclasses import dataclass
from datetime import datetime
CHANNELS=frozenset({'facebook','instagram','email','youtube','seo'})
@dataclass(frozen=True)
class Draft:
    workspace: str
    channel: str
    body: str
    planned_at: datetime

def preview(draft: Draft, allowed: frozenset[str]) -> dict:
    if not draft.workspace or draft.workspace not in allowed: raise PermissionError('Workspace not authorized')
    if draft.channel not in CHANNELS or not draft.body.strip() or len(draft.body)>5000: raise ValueError('Invalid content')
    if draft.planned_at.utcoffset() is None: raise ValueError('Timezone required')
    return {'workspace':draft.workspace,'channel':draft.channel,'body':draft.body,'planned_at':draft.planned_at.isoformat(),'status':'draft','scheduled':False,'published':False,'requires_review':True}
