"""Combined recovery audit ownership through fresh retained and hosted checks."""
import json
from types import SimpleNamespace
import pytest
from bookflow import BookflowError
from bookflow.company import payment_authority as pa
from bookflow.core import registry
from bookflow.core.context import Context,Interface
from bookflow.adapters.mcp.runtime import Runtime
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_payment_recovery import setup,declaration,call
from tests.test_work_billing_lifecycle import accepted,bill
from tests.test_row3_host import hosted as host_fixture
from tests.test_mcp_runtime import credential
from tests.payment_raw_evidence import books


@pytest.fixture
def recovery_graph(client,sale):
    draft,first,second=setup(client,sale)
    work=bill(client,accepted(client,sale))
    edits=[dict(invoice_id=work['id'],observed_invoice_version=1,action='remove')]
    begun=call(client,'begin',declaration(draft,edits))
    return dict(selection=draft['id'],recovery=begun['original_receipt']['recovery_id'],event=begun['original_receipt']['audit_event_id'],
        edits=edits,expected_before={first['id'],second['id']},work=work['id'])


@pytest.fixture
def hosted(root,recovery_graph):yield from host_fixture.__wrapped__(root)


def test_recovery_upload_changes_old_event_current_authority(hosted,recovery_graph,monkeypatch,tmp_path):
    graph=recovery_graph;cred=credential(hosted,monkeypatch);runtime=Runtime.for_host(hosted.handle.host);cmd=registry.get('audit show')
    intent=runtime.admit(cmd,Context.new(Interface.mcp,'recovery-cohort-freshness'),cred,hosted.company_id,'option',False)
    runtime.prepare_json(intent,dict(event=graph['event']),cred);document=runtime.execute_json(intent,cred)
    runtime.intents.delivery(intent);runtime.intents.finish(intent,receipt=json.dumps(document).encode(),publication=document.permit.retained())
    observations=[];init=pa._EventCohort.__init__
    def measured(self,db,events):
        init(self,db,events);observations.append((db,{k:v for k,v in self.resolved.items()}))
    monkeypatch.setattr(pa._EventCohort,'__init__',measured)
    document.check();first=observations[-1][0]
    assert observations[-1][1][graph['event']]==graph['expected_before']
    hosted.ok('payment.recovery.upload',dict(recovery_id=graph['recovery'],chunk_index=0,entries=graph['edits']),company=hosted.company_id,headers={'X-Bookflow-Reason':'Add the complete attempted target'})
    hosted.ok('payment.recovery.abort',dict(recovery_id=graph['recovery'],expected_recovery_version=2,disposition='discard_entire_attempt'),company=hosted.company_id,headers={'X-Bookflow-Reason':'Retain abandoned attempt history'})
    document.check()
    assert observations[-1][0] is not first
    assert observations[-1][1][graph['event']]==graph['expected_before']|{graph['work']}
    before=books(hosted.root)
    # Actual HTTP audit list/show/tail/activity exercise the adapted scalar/
    # cohort owners after the physical active pointer has disappeared.
    requests=[('audit.show',dict(event=graph['event'])),('audit.list',dict(record_type='payment_selection_recovery',record_id=graph['recovery'],limit=200)),
        ('audit.tail',dict(after=0,record_type='payment_selection_recovery',record_id=graph['recovery'],limit=200)),
        ('activity',dict(record_type='payment_selection',record_id=graph['selection'],limit=200))]
    outputs=[hosted.ok(name,args,company=hosted.company_id) for name,args in requests]
    assert outputs[0]==document and outputs[1]['items'] and outputs[2]['items'] and outputs[3]['items']
    gate=pa.require_resource
    def denied(s,resource,role):
        if resource=='customer-work':raise BookflowError('E_PERMISSION')
        return gate(s,resource,role)
    monkeypatch.setattr(pa,'require_resource',denied)
    with pytest.raises(BookflowError) as caught:runtime.lookup(intent.reference,cred)
    assert caught.value.code=='E_PERMISSION'
    response=hosted.call('audit.show',dict(event=graph['event']),company=hosted.company_id)
    assert response.status_code==403 and 'entries' not in response.json()
    monkeypatch.setattr(pa,'require_resource',gate)
    monkeypatch.setattr(cmd,'plan',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Retained audit replanned')))
    assert json.loads(runtime.lookup(intent.reference,cred).receipt)==document
    assert books(hosted.root)==before
    (tmp_path/'recovery-freshness.json').write_text(json.dumps(dict(expected_before=sorted(graph['expected_before']),expected_after=sorted(graph['expected_before']|{graph['work']}),outputs=outputs,denied=response.json(),raw=before),indent=2))
