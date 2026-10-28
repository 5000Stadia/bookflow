"""R107 increment 2: saved mappings and decisions (design/bulk-import.md §4, §2 "References that do not exist", §11).

Done when criterion 3 holds (next month's file runs from the saved mapping in two calls; an extra column asks once; a
missing mapped header refuses to match) and an unknown terms name is decided once. Over CLI and MCP in
tests/test_import_surfaces.py.
"""
import pytest

from bookflow import BookflowError
from tests.test_imports import active_customers, books, company_db, run_all  # noqa: F401

MONTH = 'Cust Name;Pay Terms;Limit;Town\n{rows}'


def month(*rows):
    return MONTH.format(rows=''.join(f'{name};{terms};{limit};Springfield\n' for name, terms, limit in rows))


def refusal(fn):
    with pytest.raises(BookflowError) as caught:
        fn()
    return caught.value


def first_month(books):
    """A messy file mapped by hand, an unknown terms name decided and remembered, the mapping saved."""
    text = month(('Acme', 'Net 30 days', '(1.234,50)'), ('Bolt', 'Net 15', '10'))
    preview = books.run('import preview', {'kind': 'customer', 'content': text, 'source': 'Acme export',
                                           'columns': {'Cust Name': 'name', 'Limit': 'credit_limit'}})
    assert preview['run']['state'] == 'waiting' and preview['counts'] == {'create': 1, 'waiting': 1}
    [asked] = [d for d in preview['decisions'] if d['decision'] == 'reference']
    assert (asked['value'], asked['column'], asked['rows']) == ('Net 30 days', 'Pay Terms', [1])
    assert 'Net 30' in asked['suggestions']
    decided = books.run('import decide', {'run': preview['run']['id'], 'map': ['Net 30 days=Net 30'],
                                          'remember': True})
    assert decided['run']['state'] == 'previewed' and decided['decisions'] == []
    assert run_all(books, preview['run']['id'])['counts'] == {'created': 2, 'not_run': 0}
    assert books.run('customer show', {'customer': 'Acme'})['credit_limit']['amount'] == '-1234.50'
    saved = books.run('import-mapping save', {'source': 'Acme export', 'from_run': preview['run']['id']})
    return preview, saved


def test_next_month_runs_from_the_saved_mapping_in_two_calls(books):
    _, saved = first_month(books)
    # `remember` already saved version 1 with the value map; saving the same recipe again changes nothing.
    assert saved['saved'] is False and saved['mapping']['version'] == 1
    assert saved['mapping']['value_maps'] == {'terms_id': {'net 30 days': 'Net 30'}}
    assert saved['mapping']['columns'] == {'Cust Name': 'name', 'Pay Terms': 'terms_id', 'Limit': 'credit_limit',
                                           'Town': 'billing_address.city'}
    # Next month: same headers in another order, no source, no columns: one preview, one run; the terms name is not
    # asked again.
    text = 'Town;Limit;Pay Terms;Cust Name\nSpringfield;10;Net 15;Bolt\nShelby;5;Net 30 days;Cobb\n'
    preview = books.run('import preview', {'kind': 'customer', 'content': text})
    assert preview['matched']['match'] == 'exact' and preview['run']['source'] == 'Acme export'
    assert preview['counts'] == {'already_in': 1, 'create': 1} and preview['decisions'] == []
    assert run_all(books, preview['run']['id'])['counts'] == {'already_in': 1, 'created': 1, 'not_run': 0}
    assert books.run('customer show', {'customer': 'Cobb'})['terms_id'] == \
        books.run('term show', {'term': 'Net 30'})['id']


def test_an_extra_column_asks_once_and_a_missing_mapped_one_never_matches(books):
    first_month(books)
    extra = 'Cust Name;Pay Terms;Limit;Town;Region\nDart;Net 15;1;Springfield;West\n'
    offered = books.run('import check', {'kind': 'customer', 'content': extra})
    assert offered['matched']['match'] == 'probable'
    [ask] = [d for d in offered['decisions'] if d['decision'] == 'mapping']
    assert 'new Region; missing none' in ask['message']
    # The guess alone is not used in its place: the preview is refused, offering the saved mapping.
    error = refusal(lambda: books.run('import preview', {'kind': 'customer', 'content': extra}))
    assert "probably fits" in error.message
    confirmed = books.run('import preview', {'kind': 'customer', 'content': extra, 'mapping': 'Acme export'})
    assert confirmed['matched']['match'] == 'confirmed' and confirmed['matched']['new'] == ['Region']
    assert confirmed['mapping']['unmapped'] == ['Region'] and confirmed['counts'] == {'create': 1}
    saved = books.run('import-mapping save', {'source': 'Acme export', 'from_run': confirmed['run']['id']})
    assert saved['saved'] is True and saved['mapping']['version'] == 2
    # Asked once: the same headers now match exactly.
    again = books.run('import preview', {'kind': 'customer', 'content': extra.replace('Dart', 'Echo')})
    assert again['matched']['match'] == 'exact' and again['decisions'] == []
    # A mapped header renamed: no match is offered, and naming the mapping is refused, never silently mapped.
    renamed = 'Cust Name;Pay Terms;Credit;Town;Region\nFig;Net 15;1;Springfield;West\n'
    found = books.run('import-mapping match', {'kind': 'customer', 'content': renamed})
    assert [(c['source'], c['match'], c['missing_mapped']) for c in found['candidates']] == [
        ('Acme export', 'none', ['Limit'])]
    error = refusal(lambda: books.run('import preview', {'kind': 'customer', 'content': renamed,
                                                         'mapping': 'Acme export'}))
    assert error.code == 'E_VALIDATION' and "'Limit'" in error.message
    guessed = books.run('import check', {'kind': 'customer', 'content': renamed})
    assert guessed['matched'] is None


def test_a_renamed_required_header_and_synonyms(books):
    books.run('import-mapping save', {'source': 'Names', 'from_run': books.run('import preview', {
        'kind': 'customer', 'content': 'Customer Name,E-mail\nOne,one@x.test\n'})['run']['id']})
    shown = books.run('import-mapping show', {'source': 'Names'})
    assert shown['mapping']['columns'] == {'Customer Name': 'name', 'E-mail': 'email'}  # synonyms guessed, saved
    error = refusal(lambda: books.run('import preview', {'kind': 'customer', 'content': 'Client,E-mail\nTwo,t@x\n',
                                                         'mapping': 'Names'}))
    assert "'Customer Name'" in error.message
    # Without the mapping the guess has nothing for the name: the check says the mapping is incomplete.
    checked = books.run('import check', {'kind': 'customer', 'content': 'Client,E-mail\nTwo,t@x.test\n'})
    assert [g['code'] for g in checked['problems']] == ['unmapped_required']


def test_two_sources_with_equal_headers_ask_which(books):
    text = 'Name,Phone\nA1,1\n'
    for source in ('North', 'South'):
        run = books.run('import preview', {'kind': 'customer', 'content': text, 'source': source})
        books.run('import-mapping save', {'source': source, 'from_run': run['run']['id']})
    asked = books.run('import preview', {'kind': 'customer', 'content': 'Name,Phone\nB1,2\n'})
    assert asked['matched']['match'] == 'several'
    assert {c['source'] for c in asked['matched']['candidates']} == {'North', 'South'}
    assert refusal(lambda: books.run('import run', {'run': asked['run']['id']})).code == 'E_VALIDATION'
    chosen = books.run('import preview', {'kind': 'customer', 'content': 'Name,Phone\nB1,2\n', 'source': 'South'})
    assert chosen['matched']['match'] == 'exact' and chosen['run']['source'] == 'South'
    books.run('import-mapping deactivate', {'source': 'North'})
    alone = books.run('import preview', {'kind': 'customer', 'content': 'Name,Phone\nB1,2\n'})
    assert alone['matched']['source'] == 'South'
    listed = books.run('import-mapping query', {'kind': 'customer'})['items']
    assert [(s['name'], s['active']) for s in listed] == [('North', False), ('South', True)]


def test_version_history_is_append_only(books):
    run1 = books.run('import preview', {'kind': 'customer', 'content': 'Name,Phone\nA,1\n'})
    books.run('import-mapping save', {'source': 'Hist', 'from_run': run1['run']['id']})
    run2 = books.run('import preview', {'kind': 'customer', 'content': 'Name,Phone,City\nA,1,X\n', 'source': 'Hist',
                                        'columns': {'City': 'billing_address.city'}})
    books.run('import-mapping save', {'source': 'Hist', 'from_run': run2['run']['id']})
    history = books.run('import-mapping history', {'source': 'Hist'})
    assert [v['version'] for v in history['versions']] == [2, 1] and history['source']['version'] == 2
    assert books.run('import-mapping show', {'source': 'Hist', 'version': 1})['mapping']['headers'] == ['Name', 'Phone']
    with company_db(books) as db:
        with pytest.raises(Exception):
            db.execute("UPDATE import_mappings SET mode = 'update'")
        with pytest.raises(Exception):
            db.execute('DELETE FROM import_sources')
    vendor = books.run('import preview', {'kind': 'vendor', 'content': 'Name\nV\n'})
    error = refusal(lambda: books.run('import-mapping save', {'source': 'hist', 'from_run': vendor['run']['id']}))
    assert error.code == 'E_VALIDATION' and 'customer' in error.message


def test_decisions_create_and_skip_and_their_limits(books):
    text = ('Name,Terms,Customer Type,Price Level\nA,Net 45,Retail,\nB,Net 45,Wholesale,\nC,Net 30,Retail,Gold\n'
            'D,Net 30,,Platinum\n')
    preview = books.run('import preview', {'kind': 'customer', 'content': text})
    waiting = {(d['column'], d['value']): d for d in preview['decisions']}
    assert set(waiting) == {('Terms', 'Net 45'), ('Customer Type', 'Retail'), ('Customer Type', 'Wholesale'),
                            ('Price Level', 'Gold'), ('Price Level', 'Platinum')}
    assert waiting[('Customer Type', 'Retail')]['create'] == 'from the name'
    run_id = preview['run']['id']
    # A term needs its kind: the plain create is refused, naming what to add.
    error = refusal(lambda: books.run('import decide', {'run': run_id, 'create': ['Net 45']}))
    assert error.code == 'E_VALIDATION' and 'create_input' in error.message
    error = refusal(lambda: books.run('import decide', {'run': run_id, 'map': ['Retail=Nope']}))
    assert error.code == 'E_VALIDATION'
    books.run('import decide', {'run': run_id, 'create': ['Net 45', 'Retail', 'Wholesale'],
                                'create_input': {'Net 45': {'kind': 'standard', 'due_days': 45}},
                                'skip': ['Gold']})
    left = books.run('import show', {'run': run_id})
    assert left['run']['state'] == 'waiting' and [d['value'] for d in left['decisions']] == ['Platinum']
    error = refusal(lambda: books.run('import decide', {'run': run_id, 'skip': ['Gold']}))
    assert 'names no unknown reference' in error.message  # decided once, never twice
    books.run('import decide', {'run': run_id, 'skip': ['Platinum']})
    done = run_all(books, run_id)
    assert done['counts'] == {'created': 2, 'skipped': 2, 'not_run': 0}
    assert books.run('term show', {'term': 'Net 45'})['due_days'] == 45
    assert books.run('customer show', {'customer': 'A'})['customer_type_id'] == \
        books.run('customer-type show', {'customer_type': 'Retail'})['id']
    batch = books.run('batch show', {'batch': done['run']['batch']['id']})['batch']
    assert batch['counts'] == {'created': 5}  # two customers, the term and two customer types
    # The reversal retires what the decisions made with the rows.
    books.run('import reverse', {'run': run_id})
    with company_db(books) as db:
        assert db.execute("SELECT count(*) FROM terms WHERE name = 'Net 45' AND active = 1").fetchone()[0] == 0
    assert 'Wholesale' not in {t['name'] for t in books.run('customer-type query', {})['items']}
    # Accounts and items are never created by an import.
    items = books.run('import preview', {'kind': 'item', 'content': 'Item,Type,Income Account\nI,service,Nowhere\n'})
    error = refusal(lambda: books.run('import decide', {'run': items['run']['id'], 'create': ['Nowhere']}))
    assert 'never creates account' in error.message


def test_a_look_alike_customer_is_never_created_by_a_decision(books):
    books.run('customer create', {'name': 'Acme Plumbing'})
    preview = books.run('import preview', {'kind': 'customer', 'content': 'Name,Parent\nJob 1,Acme Plumbng\n'})
    [ask] = preview['decisions']
    assert ask['look_alikes'] == ['Acme Plumbing'] and ask['suggestions'][0] == 'Acme Plumbing'
    error = refusal(lambda: books.run('import decide', {'run': preview['run']['id'], 'create': ['Acme Plumbng']}))
    assert 'looks like' in error.message
    books.run('import decide', {'run': preview['run']['id'], 'map': ['Acme Plumbng=Acme Plumbing']})
    run_all(books, preview['run']['id'])
    assert 'Acme Plumbing:Job 1' in active_customers(books)


# ------------------------------------------------------------------ the review of increment 2 (r107-inc2-review.md)

def test_remember_needs_a_named_source_and_nothing_saves_onto_the_shared_default(books):
    """Medium 6: `remember` on a run with no source once saved the maps onto `customer import`, which any customer
    file with the same headers then matched."""
    preview = books.run('import preview', {'kind': 'customer', 'content': 'Name,Terms\nAcme,Net 30 days\n'})
    assert preview['run']['source'] == 'customer import'
    error = refusal(lambda: books.run('import decide', {'run': preview['run']['id'], 'map': ['Net 30 days=Net 30'],
                                                        'remember': True}))
    assert error.details['field'] == 'remember' and 'source' in error.message
    error = refusal(lambda: books.run('import-mapping save', {'source': 'Customer Import',
                                                              'from_run': preview['run']['id']}))
    assert error.details['field'] == 'source'
    assert books.run('import-mapping query', {})['items'] == []
    books.run('import decide', {'run': preview['run']['id'], 'map': ['Net 30 days=Net 30']})  # without: fine


def test_a_remembered_decision_keys_on_the_files_own_text(books):
    """Low 11: after a value map, the decision once stored the mapped text, so next month asked again."""
    books.run('term create', {'name': 'Net 31', 'kind': 'standard', 'due_days': 31})
    text = 'Name,Terms\n{name},Net 31 days\n'
    first = books.run('import preview', {'kind': 'customer', 'content': text.format(name='A'), 'source': 'S1'})
    books.run('import decide', {'run': first['run']['id'], 'map': ['Net 31 days=Net 31'], 'remember': True})
    run_all(books, first['run']['id'])
    books.run('term update', {'term': 'Net 31', 'name': 'Net 31 old'})
    second = books.run('import preview', {'kind': 'customer', 'content': text.format(name='B')})
    [asked] = [d for d in second['decisions'] if d['decision'] == 'reference']
    assert (asked['value'], asked['file_value']) == ('Net 31', 'Net 31 days')
    books.run('import decide', {'run': second['run']['id'], 'map': ['Net 31=Net 30'], 'remember': True})
    third = books.run('import check', {'kind': 'customer', 'content': text.format(name='C')})
    assert third['ok'] is True and third['mapping']['value_maps'] == {'terms_id': {'net 31 days': 'Net 30'}}


def test_saved_mappings_and_suggestions_need_the_kinds_read_permission(books):
    """Low 9: a source's headers and value maps come from its kind's records; suggestions name another list's."""
    from pathlib import Path

    from tests.conftest import as_user, make_actor
    first_month(books)
    make_actor(Path(books.root), 'viewer')
    books.client.run('membership grant', {'user': 'viewer', 'company': books.company, 'role': 'readonly',
                                          'expected_version': 0, 'denies': ['customer']})
    viewer = as_user(Path(books.root), 'viewer')
    for name in ('import-mapping show', 'import-mapping history'):
        error = refusal(lambda: viewer.run(name, {'source': 'Acme export'}, company=books.company))
        assert error.code == 'E_PERMISSION'
    assert viewer.run('import-mapping query', {}, company=books.company)['items'] == []
    assert books.run('import-mapping query', {})['items']  # the owner reads them
    make_actor(Path(books.root), 'clerk')
    books.client.run('membership grant', {'user': 'clerk', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0, 'denies': ['term']})
    clerk = as_user(Path(books.root), 'clerk')
    out = clerk.run('import check', {'kind': 'customer', 'content': 'Name,Terms\nZed,Net 3O\n', 'source': 'Other'},
                    company=books.company)
    assert [(u['value'], u['suggestions'], u['look_alikes']) for u in out['unknown_references']] == [('Net 3O', [], [])]
    assert books.run('import check', {'kind': 'customer', 'content': 'Name,Terms\nZed,Net 3O\n',
                                      'source': 'Other'})['unknown_references'][0]['suggestions']
