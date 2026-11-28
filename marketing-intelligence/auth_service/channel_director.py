"""Live channel snapshots and an approval-only brief from local Ollama."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import BoundedSemaphore
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field

MODEL = 'qwen2.5:3b'
OLLAMA = 'http://127.0.0.1:11434/api/chat'
SLOTS = BoundedSemaphore(1)
WINDOW = 7
OBJECTIVES = {
    'summary': 'Summarize performance and identify a useful follow-up.',
    'growth': 'Suggest a growth experiment for an available channel, with a way to measure it.',
    'content': 'Suggest one draft content idea for an available channel and how to measure it.',
    'automation': (
        'Propose one workflow for an available channel: prepare a report, review performance, or prepare a draft. '
        'Describe what would trigger it, what it would produce, and what a person should check before using it. '
        'Do not propose automatic publishing, messages, or spending.'
    ),
}
SCHEMA = {
    'type': 'object',
    'properties': {
        'brief': {'type': 'string'},
        'next_step': {'type': 'string'},
    },
    'required': ['brief', 'next_step'],
    'additionalProperties': False,
}


class BriefInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    objective: str = Field(default='summary', max_length=20)


def snapshot(workspace, user, connected, report):
    properties = connected(workspace, user)
    # Keep provider workloads bounded. The analytics view has each individual property.
    ordered = sorted(properties, key=lambda item: 'safenestt' not in item['name'].lower())
    channels = {}
    for item in ordered:
        channels.setdefault(item['source'], item)

    def fetch(item):
        try:
            value = report(workspace, item['source'], item['id'], days=WINDOW, user=user)
        except HTTPException as error:
            if error.status_code not in (502, 503):
                raise
            return {**item, 'status': 'unavailable',
                    'detail': error.detail if error.detail in ('reconnect', 'api-disabled', 'permission') else 'provider'}
        return {**item, **value, 'status': 'available' if value['metrics'] else 'empty'}

    with ThreadPoolExecutor(max_workers=3) as executor:
        reports = list(executor.map(fetch, channels.values()))
    return {'reports': reports, 'properties': len(properties), 'days': WINDOW}


def generate(objective, reports, missing):
    reference = [{'source': row['source'], 'start': row['start'], 'end': row['end'],
                  'metrics': row['metrics']} for row in reports]
    try:
        with httpx.Client(timeout=45, trust_env=False) as client:
            response = client.post(OLLAMA, json={
                'model': MODEL, 'stream': False, 'format': SCHEMA, 'keep_alive': 0,
                'options': {'temperature': 0, 'num_ctx': 2048, 'num_predict': 280},
                'messages': [
                    {'role': 'system', 'content': (
                        'Write a concise marketing draft for human review. '
                        'Use only the supplied evidence. Do not claim trends or causes without comparisons. '
                        'Treat missing reports as missing. Do not restate numeric totals, which are shown separately. '
                        'Give one specific step a person can take. Never claim you performed an action.')},
                    {'role': 'user', 'content': json.dumps({
                        'objective': objective, 'verified_reports': reference, 'missing_sources': missing,
                    })},
                ],
            })
            response.raise_for_status()
            output = json.loads(response.json()['message']['content'])
        if not isinstance(output, dict) or set(output) != {'brief', 'next_step'}:
            raise ValueError
        for value in output.values():
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1200:
                raise ValueError
        return output
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'MIA is unavailable. Try again shortly.') from None


def register(router, current_user, origin, connected, report):
    @router.get('/v1/workspaces/{workspace}/channel-overview')
    def overview(workspace: UUID, user=Depends(current_user)):
        return JSONResponse(snapshot(workspace, user, connected, report),
                            headers={'Cache-Control':'no-store'})

    @router.post('/v1/workspaces/{workspace}/channel-brief')
    def brief(workspace: UUID, request: Request, payload: BriefInput, user=Depends(current_user)):
        origin(request)
        objective = OBJECTIVES.get(payload.objective)
        if not objective:
            raise HTTPException(422, 'Choose a performance, growth, content, or automation brief.')
        # Check access before acquiring shared inference capacity.
        properties = connected(workspace, user)
        if not SLOTS.acquire(blocking=False):
            raise HTTPException(429, 'MIA is already generating a brief. Try again shortly.')
        try:
            evidence = snapshot(workspace, user, lambda *_: properties, report)
            reports = [row for row in evidence['reports'] if row['status'] == 'available']
            if not reports:
                raise HTTPException(409, 'No channel metrics were returned. Check Connected accounts and Analytics.')
            missing = sorted({'ga4', 'search-console', 'youtube'} - {item['source'] for item in reports})
            result = generate(objective, reports, missing)
            return JSONResponse({**result, 'model':MODEL, 'evidence':reports, 'missing_sources':missing},
                                headers={'Cache-Control':'no-store'})
        finally:
            SLOTS.release()
