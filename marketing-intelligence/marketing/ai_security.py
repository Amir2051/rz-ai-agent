"""Tenant-bound authenticated encryption and fail-closed inference policy."""
import json,os
from dataclasses import dataclass
from decimal import Decimal
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from marketing.ai_providers import prepare,extract_text

def aad(workspace,provider):return json.dumps([workspace,provider],separators=(',',':')).encode()
def seal(key,workspace,provider,secret):
    if len(key)!=32 or not secret or len(secret)>8192:raise ValueError('Invalid credential')
    nonce=os.urandom(12);return nonce+AESGCM(key).encrypt(nonce,secret.encode(),aad(workspace,provider))
def unseal(key,workspace,provider,blob):return AESGCM(key).decrypt(blob[:12],blob[12:],aad(workspace,provider)).decode()
@dataclass(frozen=True)
class ModelPolicy:
    provider:str
    model:str
    max_output:int
    max_input_bytes:int
    input_per_million:Decimal
    output_per_million:Decimal
    privacy_verified:bool=False

def plan_request(workspace,allowed,policy,prompt,budget_cents):
    if workspace not in allowed or not policy.privacy_verified:raise PermissionError('Scope or privacy denied')
    if type(budget_cents) is not int or budget_cents<0:raise ValueError('Invalid budget')
    n=len(prompt.encode())
    if n>policy.max_input_bytes:raise ValueError('Input limit')
    rates=(policy.input_per_million,policy.output_per_million)
    if any(not r.is_finite() or r<0 for r in rates):raise ValueError('Invalid rates')
    estimate=(Decimal(n+1024)*rates[0]+Decimal(policy.max_output)*rates[1])/10000
    if estimate>budget_cents:raise PermissionError('Budget denied')
    r=prepare(policy.provider,policy.model,prompt,max_tokens=policy.max_output)
    if policy.provider=='openrouter':r['body']['provider']={'zdr':True,'data_collection':'deny','allow_fallbacks':False}
    return r,estimate

def transport(request,credential,client,*,enabled=False):
    if not enabled:raise PermissionError('Inference disabled')
    headers={'Content-Type':'application/json'}
    if request['protocol']=='anthropic':headers.update({'x-api-key':credential,'anthropic-version':'2023-06-01'})
    elif request['credential_required']:headers['Authorization']='Bearer '+credential
    response=client.post(request['endpoint'],json=request['body'],headers=headers,timeout=20,follow_redirects=False)
    if response.status_code!=200:raise RuntimeError('Provider request failed')
    return extract_text(request['protocol'],response.json())
