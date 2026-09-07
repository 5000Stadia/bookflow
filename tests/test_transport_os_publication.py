"""Current peer binding and real local framing; no live granular policy substitute."""
import socket
import threading
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.core.config import Config
from bookflow.core.context import Context, Interface, client_version
from bookflow.core.publication import OSBinding
from bookflow.commands.host_cmds import make_local_handler
from tests.test_commit_hooks import owner_host


def envelope(name, cid=None, values=None):
    return {'command':name, 'input':values or {}, 'company_selector':cid,
            'context':Context.new(Interface.python,'owned-os-witness').model_dump(mode='json')}


def test_local_current_permit_has_no_token_and_exact_former_output(owner_host):
    from bookflow.core import registry
    from bookflow.core.dispatch import execute
    host,uid,login,cid=owner_host
    ctx=Context.new(Interface.python,'owned-reference')
    expected=host.run_write(uid,login,lambda s:execute(registry.get('account list'),{},ctx,s,company_selector=cid))
    out=make_local_handler(host,client_version())(login,envelope('account list',cid))
    assert dict(out)==expected and out.permit.token is None
    assert isinstance(out.credential,OSBinding)
    out.check()
    changed=make_local_handler(host,client_version())(login,envelope('company update',cid,{'fax':'own write'}))
    assert changed['changed_fields']==['fax']
    changed.check()  # own write requires ordinary current proof, no epoch exemption


def test_os_binding_rechecked_on_actual_writer_dequeue(owner_host):
    from bookflow.adapters.http.execution import run_hosted
    from bookflow.core import registry
    host,uid,login,cid=owner_host
    binding=OSBinding.capture(host,login)
    # A fixture mapping change, not a claim that granular administration is live.
    cfg=Config.load(host.data_root/'config.toml');cfg.set_user(login,'unmapped-current-user');cfg.save()
    ctx = Context.new(Interface.python, 'stale-peer')
    with pytest.raises(BookflowError) as exc:
        run_hosted(host,registry.get('company update'),{'fax':'must not apply'},
                   ctx,binding,cid,'option',False)
    assert exc.value.code=='E_UNAUTHENTICATED'
    assert host.submit(lambda:host._hub.raw.execute("SELECT count(*) FROM audit_events WHERE request_id=?",(ctx.request_id,)).fetchone()[0])==0


def test_os_remap_after_read_releases_no_local_prefix(owner_host):
    from bookflow.adapters.http.local import LocalListener
    from bookflow.core.transfer_protocol import send_json
    host,uid,login,cid=owner_host
    original=make_local_handler(host,client_version());read=threading.Event();resume=threading.Event();calls=[]
    def handler(peer,request):
        out=original(peer,request);calls.append(out);read.set();assert resume.wait(3);return out
    a,b=socket.socketpair();listener=LocalListener(host,Path('/unused-owned-local-path'),handler)
    thread=threading.Thread(target=listener._serve_one,args=(a,));thread.start()
    try:
        send_json(b,envelope('company show',cid))
        assert read.wait(3)
        cfg=Config.load(host.data_root/'config.toml');cfg.set_user(login,'different-current-user');cfg.save()
        resume.set();b.settimeout(3)
        assert b.recv(65536)==b''
        assert len(calls)==1
    finally:resume.set();b.close();thread.join(3)
    assert not thread.is_alive()


def test_local_validation_error_keeps_its_guard(owner_host):
    host,uid,login,cid=owner_host
    with pytest.raises(BookflowError) as exc:
        make_local_handler(host,client_version())(login,envelope('company update',cid,{'unknown_field':True}))
    assert exc.value.code=='E_VALIDATION'
    document=exc.value.publication_document
    assert document['code']=='E_VALIDATION' and document['details']['fields']
    document.check()


@pytest.mark.parametrize('kind', ['agent', 'system'])
def test_os_active_mapping_preserves_existing_nonhuman_eligibility(owner_host, kind):
    from tests.conftest import make_actor
    host, uid, login, cid = owner_host
    if kind == 'agent':
        target = host.submit(lambda: make_actor(host.data_root, 'os-bound-agent', kind=kind))
    else:
        target = host.submit(lambda: host._hub.raw.execute("SELECT id FROM users WHERE kind='system'").fetchone()[0])
    cfg = Config.load(host.data_root/'config.toml'); cfg.set_user(login, target); cfg.save()
    binding = OSBinding.capture(host, login)
    assert binding.user_id == target and binding.actor_kind == kind and binding.on_behalf_of is None
    binding.revalidate_current(host)
