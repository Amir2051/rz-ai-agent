"""Provider-independent request construction; no network or credential access."""
from dataclasses import dataclass
from urllib.parse import urlsplit
@dataclass(frozen=True)
class Provider:
    protocol:str
    endpoint:str
PROVIDERS={
 'openai':Provider('openai','https://api.openai.com/v1/chat/completions'),
 'anthropic':Provider('anthropic','https://api.anthropic.com/v1/messages'),
 'openrouter':Provider('openai','https://openrouter.ai/api/v1/chat/completions'),
 'gemini':Provider('openai','https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
 'ollama':Provider('openai','http://127.0.0.1:11434/v1/chat/completions'),
}
def prepare(provider,model,prompt,*,max_tokens=512,custom_endpoint=None,approved_endpoints=frozenset()):
    if not isinstance(model,str) or not model.strip() or len(model)>200 or not isinstance(prompt,str) or not prompt.strip() or len(prompt)>16000: raise ValueError('Invalid request')
    if type(max_tokens) is not int or not 1<=max_tokens<=2048: raise ValueError('Invalid token limit')
    if provider=='custom':
        if not custom_endpoint or custom_endpoint not in approved_endpoints: raise PermissionError('Endpoint not approved')
        u=urlsplit(custom_endpoint)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment: raise ValueError('Invalid endpoint')
        p=Provider('openai',custom_endpoint)
    else:
        if custom_endpoint is not None or provider not in PROVIDERS: raise ValueError('Unknown provider or override')
        p=PROVIDERS[provider]
    body={'model':model,'messages':[{'role':'user','content':prompt}],'max_tokens':max_tokens}
    return {'provider':provider,'protocol':p.protocol,'endpoint':p.endpoint,'body':body,'execution_enabled':False,'credential_required':provider!='ollama'}
def extract_text(protocol,response):
    if protocol=='anthropic':
        return '\n'.join(b['text'] for b in response['content'] if b.get('type')=='text')
    if protocol=='openai':
        content=response['choices'][0]['message']['content']
        if not isinstance(content,str): raise ValueError('Expected text response')
        return content
    raise ValueError('Unknown protocol')
