from datetime import date, datetime
from uuid import UUID
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from marketing.planning import Campaign, plan
from marketing.content import Draft, preview
from marketing.workflows import Lead, lead_preview, automation
class DraftInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['campaign','content','lead','automation']
    title: str=Field(min_length=1,max_length=120)
    channel: str='facebook'
    objective: str='awareness'
    start: date|None=None
    end: date|None=None
    planned_at: datetime|None=None
    body: str=Field(default='',max_length=5000)
    consent: bool=False
    task: str='report'
def build(workspace: UUID, data: DraftInput):
    w=str(workspace); allowed=frozenset({w})
    try:
        if data.kind=='campaign':
            if data.start is None or data.end is None: raise ValueError()
            result=plan(Campaign(w,data.title,data.channel,data.objective,data.start,data.end),allowed)
            result.update(start=data.start.isoformat(),end=data.end.isoformat())
        elif data.kind=='content':
            if data.planned_at is None: raise ValueError()
            result=preview(Draft(w,data.channel,data.body,data.planned_at),allowed)
        elif data.kind=='lead': result=lead_preview(Lead(w,data.title,data.consent),allowed)
        else:
            result=automation(w,data.task,data.planned_at,allowed)
            if data.body.strip():
                result['notes']=data.body.strip()
    except (ValueError,PermissionError): raise HTTPException(422,'Invalid draft fields') from None
    return {**result,'title':data.title,'external_execution':False}
