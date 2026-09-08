"""Actual company activity renders notes and resumes through owned wire output."""
from bookflow.core import history_wire, history_cursors, identity_admin_binding as binding
from bookflow.core.context import Context
from bookflow.core.publication_audit import execute_history, revalidate_proof
from bookflow.hub.audit_projection import HistorySelection
from tests.test_audit_projection_activity import world, customer, note, edited, hosted, storage


def test_activity_wire_pages_preserve_notes_and_storage(hosted,customer):
    host,cred,cid,path=hosted
    selection=HistorySelection(mode='activity',company=cid,record_type='customer',record_id=customer['id'],limit=1)
    before=storage(path);seen=[];bodies=[];token=None
    for index in range(3):
        ctx=Context.new('http','Activity wire witness')
        with binding.hosted_reader(host,cred,request_id=ctx.request_id) as reader:
            current=history_cursors.resume(reader,selection,token,ctx=ctx) if token else selection
            _,semantic=execute_history(reader,current,ctx=ctx)
            output,proof=history_wire.bind(reader,semantic)
            assert output['projection_version']==2 and output['count']==1
            assert proof.matches(output)
            item=output['items'][0];identity=(item['event_id'],item['entry_id'])
            assert identity not in seen;seen.append(identity);bodies.append(item['body'])
            assert not {'seq','high_water','next_entry_anchor','next_anchor'} & output.keys()
            token=output['next_cursor']
            assert bool(token)==output['has_more']==(index<2)
            revalidate_proof(reader,proof)
        assert host._readers_attached==0
    assert 'Corrected exactly' in bodies
    assert storage(path)==before
