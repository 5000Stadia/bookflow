"""Candidate scans preserve public pages/bookmarks across mixed-entry events."""
import pytest

from bookflow.adapters.http.execution import run_hosted
from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.hub import audit_projection as projection
from tests.test_audit_projection_activity import world
from tests.test_audit_projection_selection import selected


@pytest.mark.parametrize('filter_mode',['kind','id','both'])
def test_candidate_pages_match_full_projection(selected,monkeypatch,filter_mode):
    host,credential,company,_,histories,draft=selected
    filters={}
    if filter_mode in ('kind','both'):filters['record_type']='payment_selection'
    if filter_mode in ('id','both'):filters['record_id']=draft['id']
    # Actual selection updates contain header, revision and item identities.
    # A matching header must keep the whole projected event, including its
    # other identities. The projection suite pins raw identity preservation.
    assert any(len(rows)>1 for _,rows in histories)
    original=projection._visible_events
    counts={'candidate':0,'full':0}
    active='candidate'
    def scan(*args,**kwargs):
        if active=='full':kwargs.pop('candidate',None)
        for row in original(*args,**kwargs):
            counts[active]+=1
            yield row
    monkeypatch.setattr(projection,'_visible_events',scan)
    command=registry.get('audit list')
    ctx=Context.new('http','Candidate equivalence')
    before=None
    for _ in range(2):
        fields={**filters,'limit':1}
        if before:fields['before']=before
        active='candidate'
        optimized=run_hosted(host,command,fields,ctx,credential,company,'option',False)
        active='full'
        baseline=run_hosted(host,command,fields,ctx,credential,company,'option',False)
        assert optimized==baseline
        active='candidate'
        baseline.check()
        optimized.check()
        assert optimized['count']==1 and optimized['next_before']
        before=optimized['next_before']
    assert counts['candidate']>0 and counts['full']>0
    assert host._readers_attached==0
