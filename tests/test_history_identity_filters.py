"""Actor-name filters use only the identity fields the reader may see."""
from bookflow.hub.audit_projection import HistorySelection, make_audience, normalized_selection
from bookflow.core.publication_audit import execute_history
from bookflow.core.history_cursors import issue
from tests.test_history_cursors import world, observe


def test_visible_actor_username_and_id_select_same_events_and_bookmark(world):
    _,_,first,second=world
    with observe(world,'A') as reader:
        by_name,proof=execute_history(reader,HistorySelection(mode='list',actor='a',limit=1))
        token=issue(reader,proof)
        by_id,other=execute_history(reader,HistorySelection(mode='list',actor='A',limit=1))
        assert by_name==by_id
        assert by_id['events'][0]['id']==second
        assert issue(reader,other)==token


def test_hidden_usernames_do_not_become_resolved_identity_filters(world):
    with observe(world,'R') as reader:
        audience=make_audience(reader)
        own=normalized_selection(audience,HistorySelection(mode='list',actor='r',principal='r'))
        assert own.actor==own.principal=='R'
        hidden=normalized_selection(audience,HistorySelection(mode='list',actor='u',principal='u'))
        assert hidden.actor==hidden.principal=='u'
        output,_=execute_history(reader,hidden)
        assert output['events']==[]
