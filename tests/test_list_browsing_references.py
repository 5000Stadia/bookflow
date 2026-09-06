"""Saved filter normalization and complete bounded reference label batches."""
import json
from fastapi.testclient import TestClient
from tests.test_row3_host import hosted,PASSWORD


def page_data(response):
    assert response.status_code==200,response.text
    return json.loads(response.text.split('id="browse-data">',1)[1].split('</script>',1)[0])


def browser(hosted):
    api=TestClient(hosted.handle.app)
    assert api.post('/login',json={'username':hosted.login,'password':PASSWORD}).status_code==200
    return api


def test_saved_reference_filter_matches_shared_whitespace_normalization(hosted):
    term=hosted.ok('term.create',{'name':'Saved link term','kind':'standard','due_days':11},company=hosted.company_id)
    vendor=hosted.ok('vendor.create',{'name':'Saved link vendor','terms_id':term['id']},company=hosted.company_id)
    raw=' terms_id='+term['id']+' '
    shared=hosted.ok('vendor.query',{'filter':[raw]},company=hosted.company_id)
    assert [r['id'] for r in shared['items']]==[vendor['id']]
    data=page_data(browser(hosted).get(f'/c/{hosted.company_id}/vendor',params={'filter':raw}))
    assert data['criteria'][0]['display']=='Saved link term'
    assert data['criteria'][0]['legacy']=='terms_id='+term['id']


def test_reference_labels_are_complete_across_64_id_boundary(hosted):
    terms=[hosted.ok('term.create',{'name':f'Batch reference {i:02d}','kind':'standard','due_days':i},company=hosted.company_id) for i in range(65)]
    params=[('filter','terms_id='+r['id']) for r in terms]
    data=page_data(browser(hosted).get(f'/c/{hosted.company_id}/vendor',params=params))
    assert [c['display'] for c in data['criteria']]==[r['name'] for r in terms]
    assert [c['legacy'] for c in data['criteria']]==[value for _,value in params]
