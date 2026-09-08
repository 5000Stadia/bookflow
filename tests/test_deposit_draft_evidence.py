"""Pure retained graph collection through real draft and selection producers."""
import pytest
from bookflow.company.deposit_draft_evidence import collect, DraftGraph
from bookflow.core.errors import BookflowError
from tests.test_deposit_drafts import cash, driver, run, sale


def test_removed_sources_and_abandoned_child_remain_in_graph(cash, driver, run):
    draft = run('create', {})
    with driver.session() as s:
        assert collect(s.company, draft=draft.id) == DraftGraph((), (draft.id,), ())
    draft = run('update', dict(draft=draft.id, expected_version=draft.version, set_sources=[cash]))
    child = run('create', dict(draft=draft.id, expected_version=draft.version), True)
    child = run('clear', dict(selection=child.id, expected_version=child.version), True)
    child = run('abandon', dict(selection=child.id, expected_version=child.version), True)
    draft = run('clear', dict(draft=draft.id, expected_version=draft.version))
    assert draft.summary.source_count == child.source_count == 0
    with driver.session() as s:
        before = tuple(s.company.raw.iterdump())
        expected = DraftGraph((cash['source'],), (draft.id,), (child.id,))
        assert collect(s.company, draft=draft.id) == expected
        assert collect(s.company, selection=child.id) == expected
        assert tuple(s.company.raw.iterdump()) == before
        with pytest.raises(BookflowError) as caught:
            collect(s.company, draft='00000000000000000000000000')
        assert caught.value.code == 'E_RECORD_NOT_FOUND'
