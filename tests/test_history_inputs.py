"""Public history vocabulary translates without numeric continuation fallback."""
import pytest
from pydantic import ValidationError
from bookflow.core.errors import BookflowError
from bookflow.core.history_inputs import AuditListInput, AuditTailInput, ActivityInput, EventSelector, command_request


def test_command_filters_keep_user_meaning_in_closed_selection():
    query=AuditTailInput(actor='Pat',kind='human',via='mcp',command='invoice update',after='opaque.token',scan_limit=10)
    request=command_request('tail',query,company='C')
    assert request.bookmark=='opaque.token'
    assert request.selection.company=='C' and request.selection.actor=='Pat'
    assert request.selection.actor_kind=='human' and request.selection.interface=='mcp'
    assert request.selection.command=='invoice update' and request.selection.scan_limit==10
    assert request.selection.anchor is None and not request.selection.empty_start
    activity=command_request('activity',ActivityInput(record_type='customer',record_id='CUSTOMER',kinds=['note'],cursor='opaque.token'),company='C')
    assert activity.selection.kinds==('note',) and activity.bookmark=='opaque.token'
    show=command_request('show',EventSelector(event='EVENT'))
    assert show.selection.event=='EVENT' and show.bookmark is None


@pytest.mark.parametrize('value',(7,False,'',' padded ','x'*4097))
def test_invalid_bookmark_shapes_have_fixed_restart_error(value):
    for cls,key,extra in ((AuditListInput,'before',{}),(AuditTailInput,'after',{}),(ActivityInput,'cursor',{'record_type':'customer','record_id':'C'})):
        with pytest.raises(BookflowError) as caught:cls(**extra,**{key:value})
        assert caught.value.details=={'reason':'invalid_cursor'}
        assert caught.value.message=='Restart the history query without a bookmark.'


def test_no_request_version_or_internal_anchor_escape():
    for extra in ({'projection_version':2},{'anchor':'raw-event'},{'endpoint':'raw-event'},{'empty_start':True}):
        with pytest.raises(ValidationError):AuditTailInput(**extra)
    assert command_request('tail',AuditTailInput()).bookmark is None
