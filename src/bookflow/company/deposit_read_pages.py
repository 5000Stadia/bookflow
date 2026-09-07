"""Company-keyed, binding-scoped private read continuations; no key creation."""
import base64,hashlib,hmac,json
from bookflow.company import deposit_dependency_history as history
from bookflow.company.ledger_reports import _cursor_key
from bookflow.core.errors import BookflowError


def _scope(s,binding,purpose):
    identity=history.execution_binding(s,binding)
    return _cursor_key(s.company), ('bookflow.deposit.read.'+purpose+'.v1\0').encode()+history.canonical([s.company_row['id'],identity]).encode()+b'\0'


def fingerprint(s,binding,purpose,content):
    key,scope=_scope(s,binding,purpose)
    return hmac.new(key,scope+b'facts\0'+history.canonical(content).encode(),hashlib.sha256).hexdigest()


def encode(s,binding,purpose,fp,position):
    key,scope=_scope(s,binding,purpose)
    raw=history.canonical(dict(v=1,purpose=purpose,fp=fp,position=position)).encode()
    return base64.urlsafe_b64encode(raw+hmac.digest(key,scope+raw,'sha256')).decode().rstrip('=')


def decode(s,binding,purpose,token):
    try:
        key,scope=_scope(s,binding,purpose)
        if type(token) is not str or not 1<=len(token)<=4096:raise ValueError()
        data=base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True)
        raw,mac=data[:-32],data[-32:]
        if len(mac)!=32 or not hmac.compare_digest(mac,hmac.digest(key,scope+raw,'sha256')):raise ValueError()
        value=json.loads(raw)
        if set(value)!= {'v','purpose','fp','position'} or type(value['v']) is not int or value['v']!=1 or value['purpose']!=purpose or type(value['fp']) is not str or len(value['fp'])!=64:raise ValueError()
        return value
    except (ValueError,TypeError,KeyError):raise BookflowError('E_VALIDATION',details={'field':'cursor'}) from None


def page(s,binding,purpose,values,content,limit,cursor,*,pin=None):
    fp=fingerprint(s,binding,purpose,content);offset=0
    if cursor:
        prior=decode(s,binding,purpose,cursor)
        if prior['fp']!=fp:raise BookflowError('E_QUERY_STALE')
        position=prior['position']
        if not isinstance(position,list) or len(position)!=2 or position[0]!=pin or type(position[1]) is not int or not 0<=position[1]<len(values):
            raise BookflowError('E_VALIDATION',details={'field':'cursor'})
        offset=position[1]
    chunk=values[offset:offset+limit];after=offset+len(chunk)
    next_token=encode(s,binding,purpose,fp,[pin,after]) if after<len(values) else None
    previous=encode(s,binding,purpose,fp,[pin,max(0,offset-limit)]) if offset else None
    return tuple(chunk),fp,next_token,previous
