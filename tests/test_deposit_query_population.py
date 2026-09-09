"""Opt-in measured public query checkpoint; no performance cache or fixture cap."""
import json
import os
from time import perf_counter

import pytest

from tests.test_deposit_command import books, COMPANY  # noqa: F401
from tests.test_deposit_public_query import post, query
from tests.test_row3_host import Hosted
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version


@pytest.mark.skipif(os.environ.get('BOOKFLOW_MEASURE_DEPOSIT_QUERY')!='1',reason='Explicit population measurement only')
@pytest.mark.timeout(360)
def test_actual_populations(books,tmp_path,monkeypatch):
    from bookflow.company import deposit_public_reads as reads, deposit_read_facts as facts, deposit_read_pages as pages
    client=books['client'];root=tmp_path/'root'
    cid=client.company.list()['items'][0]['company_id']
    issued=client.token.issue(label='Disposable query measurement')
    completed=0;measurements=[]
    for population in (1,25,100):
        start=perf_counter()
        while completed<population:
            post(books,'population-'+str(completed));completed+=1
        setup=perf_counter()-start
        phases={}
        def wrap(owner,name,label):
            original=getattr(owner,name)
            def measured(*args,**kwargs):
                began=perf_counter()
                try:return original(*args,**kwargs)
                finally:phases[label]=phases.get(label,0)+(perf_counter()-began)
            patch.setattr(owner,name,measured)
        with monkeypatch.context() as patch:
            wrap(reads,'_query_admitted','candidate_admission_s')
            wrap(facts,'load_complete','financial_load_s')
            wrap(pages,'page','paging_s')
            start=perf_counter();native=query(books,page=dict(limit=25));native_time=perf_counter()-start
            native_phases=dict(phases);phases.clear()
            handle=start_serving(root,client_version(),bind='127.0.0.1:8765',secure_cookies=False)
            try:
                hosted=Hosted(handle,root,'',cid,issued,'',{})
                start=perf_counter();http=hosted.ok('deposit.query',dict(page=dict(limit=25)),company=cid);http_time=perf_counter()-start
            finally:handle.stop()
        for result in (native,http):
            assert result['total_count']==population
            assert len(result['items'])==min(population,25)
            assert result['totals']['bank_total']['minor_units']==population*2500
            assert result['effective_bank_total']['minor_units']==population*2500
            assert bool(result['next_cursor'])==(population>25)
        assert native['items']==http['items'] and native['totals']==http['totals']
        record=dict(population=population,added_setup_s=setup,native_s=native_time,http_s=http_time,
            native_phases=native_phases,http_phases=phases,matched_count=http['total_count'],bank_minor_units=http['totals']['bank_total']['minor_units'])
        measurements.append(record)
        (tmp_path/'population-times.json').write_text(json.dumps(measurements,indent=2)+'\n')
        print(json.dumps(record),flush=True)
