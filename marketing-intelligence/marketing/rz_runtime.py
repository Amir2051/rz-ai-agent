"""Restricted marketing director orchestration.

Model output is returned as a reviewable draft and is never executed. When
inference is enabled a durable budget reservation must be supplied by the
caller; failed or ambiguous calls release the reservation.
"""
from marketing.ai_security import plan_request, transport
from marketing.workflows import authorize_action
from marketing.ai_budget import reserve, release

TOOLS = frozenset({'marketing.preview', 'marketing.report', 'marketing.draft'})


def tool_allowed(workspace, allowed, name):
    if name not in TOOLS:
        raise PermissionError('Tool denied')
    return authorize_action(workspace, name.split('.')[1], 0, allowed)


def run_director(workspace, allowed, policy, prompt, budget_cents, credential, client, *,
                 enabled=False, conn=None):
    if enabled and conn is None:
        raise PermissionError('Durable budget ledger required for live inference')
    request, estimate = plan_request(workspace, allowed, policy, prompt, budget_cents)
    result = {'workspace': workspace, 'draft': None, 'requires_review': True,
              'external_execution': False, 'estimated_cost_cents': str(estimate),
              'budget_reserved': False}
    if not enabled:
        result['draft'] = transport(request, credential, client, enabled=False)
        return result
    if conn is None:
        raise PermissionError('Durable budget ledger required for live inference')
    microusd = max(1, int(estimate * 10000))
    reserve(conn, workspace, microusd)
    result['budget_reserved'] = True
    try:
        text = transport(request, credential, client, enabled=True)
    except Exception:
        release(conn, workspace, microusd)
        result['budget_reserved'] = False
        raise
    result['draft'] = text
    return result
