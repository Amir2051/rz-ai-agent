"""Offline prototype: explicit synthetic read capabilities only."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Scope:
    business: str
    account: str

READ_TOOLS = frozenset({'synthetic.ga4.summary', 'synthetic.gsc.summary', 'synthetic.meta.summary'})

def authorize(tool: str, scope: Scope, allowed: frozenset[Scope]) -> None:
    if tool not in READ_TOOLS or scope not in allowed or not scope.business or not scope.account:
        raise PermissionError('Tool or account not authorized')

def execute(tool: str, scope: Scope, allowed: frozenset[Scope]) -> dict:
    authorize(tool, scope, allowed)
    return {'source':tool, 'business':scope.business, 'account':scope.account, 'synthetic':True, 'rows':[]}
