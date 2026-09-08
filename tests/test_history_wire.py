"""Exact wire publication and reconnect from a partially transferred tail page."""
import pytest
from bookflow.core import history_wire, history_cursors, registry
from bookflow.core.publication_audit import execute_history, revalidate_proof
from bookflow.core.publication import PublicationPermit
from bookflow.core.context import Context
from bookflow.hub.audit_projection import HistorySelection
from tests.test_history_cursors import world, observe
from tests.test_audit_projection_publication import credential


def test_tail_each_event_has_its_own_resume_bookmark(world):
    host,_,first,second=world
    selection=HistorySelection(mode='tail',limit=2,empty_start=True)
    with observe(world) as reader:
        _,semantic=execute_history(reader,selection)
        output,proof=history_wire.bind(reader,semantic)
        assert output['projection_version']==2
        assert [x['id'] for x in output['items']]==[first,second]
        assert output['items'][0]['resume_after']!=output['items'][1]['resume_after']
        assert not {'seq','high_water','endpoint','next_anchor'} & output.keys()
        assert all('seq' not in x and x['entries'] is None for x in output['items'])
        assert proof.matches(output)
        altered=dict(output,count=99)
        assert not proof.matches(altered)
        registry.load_all();cmd=registry.get('hub audit tail')
        ctx=Context.new('http','Wire witness').model_copy(update={'request_id':'REQUEST'})
        permit=PublicationPermit(cmd,None,ctx,(proof.identity.actor,proof.identity.actor_kind,proof.identity.hub_admin),frozenset(),None,None)
        permit.finish(reader.session,result=output,audit_proof=proof)
        retained=PublicationPermit.from_retained(permit.retained())
    retained.check(host,credential(host,'R'))
    with observe(world) as reader:
        resumed=history_cursors.resume(reader,HistorySelection(mode='tail',limit=2),output['items'][0]['resume_after'])
        following,_=execute_history(reader,resumed)
        assert [x['id'] for x in following['events']]==[second]
        revalidate_proof(reader,proof)
    assert host._readers_attached==0


@pytest.mark.parametrize('mode',('list','show'))
def test_wire_list_and_show_preserve_projected_count_and_shape(world,mode):
    _,_,_,event=world
    selection=HistorySelection(mode=mode,**({'event':event} if mode=='show' else {'limit':1}))
    with observe(world) as reader:
        _,semantic=execute_history(reader,selection)
        output,proof=history_wire.bind(reader,semantic)
        assert output['projection_version']==2 and proof.matches(output)
        shown=output if mode=='show' else output['items'][0]
        assert shown['entry_count']==len(semantic.history.events[0].entries)
        if mode=='show':assert shown['entries'] and 'next_before' not in output
        else:assert shown['entries'] is None and type(output['next_before']) is str
        revalidate_proof(reader,proof)


def test_wire_bookmark_authority_rechecked_before_publication(world):
    from bookflow.core.errors import BookflowError
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.test_audit_projection_publication import apply
    host,path,*_=world
    # Empty fresh-tail output has no event dependency to accidentally enforce
    # this check: the issued bookmark itself must retain current authority.
    with observe(world) as reader:
        _,semantic=execute_history(reader,HistorySelection(mode='tail',limit=1))
        output,proof=history_wire.bind(reader,semantic)
        assert output['items']==[] and output['next_after']
    apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(3),'readonly'),'A')
    with observe(world) as reader:
        with pytest.raises(BookflowError) as caught:revalidate_proof(reader,proof)
        assert caught.value.details['reason']=='authority_changed'
        _,fresh=execute_history(reader,HistorySelection(mode='tail',limit=1))
        current,_=history_wire.bind(reader,fresh)
        assert current['items']==[] and current['next_after']!=output['next_after']
    assert host._readers_attached==0
