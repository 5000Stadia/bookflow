"""Bounded activity excerpts preserve admitted instructions and sealed identity."""
import json
import pytest
from bookflow.hub.audit_projection import (
    AuditIdentity,ProjectedEntry,ProjectedEvent,ProjectedExplanation,ProjectedDirective,activity_item)
from bookflow.hub.audit_projection_legacy import NoteView
from bookflow.commands.activity_cmds import PAGE_BYTES


def event_with(explanation,body=None):
    view=None
    if body is not None:
        view=NoteView(id='note',version=1,created_at='2026-09-08T00:00:00Z',created_by=None,
            created_via='mcp',updated_at='2026-09-08T00:00:00Z',updated_by=None,updated_via='mcp',
            record_type='customer',record_id='customer',body=body,author_id=None,interface='mcp',
            at='2026-09-08T00:00:00Z',edited_at=None,kind='note')
    entry=ProjectedEntry('entry',AuditIdentity('company','note' if body is not None else 'customer','record'),
        'create',None,view,(),None,1)
    event=ProjectedEvent('event','2026-09-08T00:00:00Z','customer create','Created',
        'human','Human','human',None,None,'mcp',(entry,),explanation)
    return event,entry


@pytest.mark.parametrize('explanation',[
    None,ProjectedExplanation(None,'not_cited',None),
    ProjectedExplanation('Why','unavailable',None),
    ProjectedExplanation('Why','available',ProjectedDirective('directive','D1','Do X unless Y'))])
def test_activity_preserves_admitted_states(explanation):
    event,entry=event_with(explanation)
    item=activity_item(event,entry)
    assert item.explanation==explanation and not item.text_truncated
    assert item==activity_item(event,entry)


def test_equal_multibyte_narratives_have_fixed_priority_without_partial_directive():
    text='界\\"'*14000
    instruction='Do X unless Y'
    explanation=ProjectedExplanation(text,'available',ProjectedDirective('directive','D1',instruction))
    event,entry=event_with(explanation,body=text)
    item=activity_item(event,entry)
    assert len(json.dumps(item.model_dump()).encode())<=PAGE_BYTES-8192
    assert item.text_truncated and len(item.body)<len(text)
    assert item.explanation.reason==text
    assert item.explanation.directive.text==instruction
    assert item==activity_item(event,entry)
    assert event.explanation==explanation and entry.after.body==text


def test_oversized_instruction_is_omitted_whole_and_full_event_is_unchanged():
    instruction='Do X unless Y. 界'*50000
    explanation=ProjectedExplanation('Reason','available',ProjectedDirective('directive','D1',instruction))
    event,entry=event_with(explanation)
    item=activity_item(event,entry)
    assert len(json.dumps(item.model_dump()).encode())<=PAGE_BYTES-8192
    assert item.text_truncated
    assert item.explanation.directive==ProjectedDirective('directive','D1','')
    assert item.explanation.directive_status=='available'
    assert event.explanation.directive.text==instruction
    assert item==activity_item(event,entry)


def test_reduction_order_prefers_narrative_over_instruction_and_ignores_size():
    body='body'*10
    reason='界'*60000
    instruction='Do X unless Y'
    explanation=ProjectedExplanation(reason,'available',ProjectedDirective('d','D1',instruction))
    event,entry=event_with(explanation,body=body)
    item=activity_item(event,entry)
    assert item.explanation.directive.text==instruction
    assert item.body==''
    assert item.explanation.reason and len(item.explanation.reason)<len(reason)
