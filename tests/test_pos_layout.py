"""R114 increment 6: shared ordered layouts, versioned edits, and picture ownership."""
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
import bookflow
from tests.test_pos_sale import Company
from tests.test_item_pictures import png


@pytest.fixture
def co(root):
    co = Company(root)
    info = co.run('company show')
    co.run('company update', {'pos_enabled': True, 'expected_version': info['info_version']})
    return co


def edit(co, verb, **fields):
    fields.setdefault('expected_version', co.run('pos-box list')['version'])
    return co.run('pos-box ' + verb, fields)


def group(co, label, parent=None):
    result = edit(co, 'create', kind='group', label=label, parent=parent)
    return next(b['id'] for b in result['items'] if b['label'] == label)


def test_category_seed_is_read_only_stable_and_persists_on_first_edit(co, root):
    first = co.run('pos-box list')
    assert first['version'] == 0 and not first['initialized'] and first['items']
    assert first == co.run('pos-box list')
    box = next(b for b in first['items'] if b['kind'] == 'group')
    saved = edit(co, 'update', pos_box=box['id'], colour='#123456')
    assert saved['initialized'] and saved['version'] == 1
    assert {b['id'] for b in first['items']} == {b['id'] for b in saved['items']}
    another_device = Company(root)
    assert another_device.run('pos-box list') == saved
    assert next(b for b in saved['items'] if b['id'] == box['id'])['colour'] == '#123456'


def test_nested_groups_reorder_duplicate_item_and_remove_leave_inventory_untouched(co):
    edit(co, 'seed', mode='empty')
    before = co.run('item show', {'item': 'Pipe Sealant'})
    one = group(co, 'One')
    two = group(co, 'Two')
    nested = group(co, 'Nested', one)
    first = edit(co, 'create', kind='item', parent=nested, item=before['id'])
    first_id = next(b['id'] for b in first['items'] if b['kind'] == 'item')
    second = edit(co, 'create', kind='item', parent=two, item=before['id'])
    second_id = next(b['id'] for b in second['items'] if b['kind'] == 'item' and b['id'] != first_id)
    moved = edit(co, 'move', pos_box=two, parent=None, position=0)
    homes = sorted((b for b in moved['items'] if b['parent'] is None), key=lambda b: b['position'])
    assert [b['id'] for b in homes] == [two, one]
    moved = edit(co, 'move', pos_box=first_id, parent=two, position=0)
    children = sorted((b for b in moved['items'] if b['parent'] == two), key=lambda b: b['position'])
    assert [b['id'] for b in children] == [first_id, second_id]
    edit(co, 'remove', pos_box=one)
    result = edit(co, 'remove', pos_box=two)
    assert result['items'] == [] and result['initialized']
    assert co.run('pos-box list')['items'] == []
    assert co.run('item show', {'item': before['id']}) == before


def test_cycles_and_item_parent_and_non_sales_items_are_refused(co):
    edit(co, 'seed', mode='empty')
    one = group(co, 'One')
    two = group(co, 'Two', one)
    third = group(co, 'Third', two)
    for parent in (one, two, third):
        assert co.fails('pos-box move', dict(pos_box=one, parent=parent, position=0,
            expected_version=co.run('pos-box list')['version'])).code == 'E_VALIDATION'
    result = edit(co, 'create', kind='item', item='Pipe Sealant')
    item_box = next(b['id'] for b in result['items'] if b['kind'] == 'item')
    assert co.fails('pos-box create', dict(kind='group', label='No', parent=item_box,
        expected_version=result['version'])).code == 'E_VALIDATION'
    assert co.fails('pos-box create', dict(kind='item', item='Work Order Subtotal',
        expected_version=result['version'])).code == 'E_VALIDATION'
    assert co.fails('pos-box move', dict(pos_box=item_box, position=100,
        expected_version=result['version'])).code == 'E_VALIDATION'
    assert co.run('pos-box list') == result


@pytest.mark.parametrize('style', ['block', 'list', 'compact', 'photo'])
def test_styles_are_shared_per_group_and_home_without_changing_company_settings_version(co, root, style):
    edit(co, 'seed', mode='empty')
    one = group(co, 'One')
    two = group(co, 'Two')
    settings_version = co.run('company show')['info_version']
    edit(co, 'view', pos_box=one, style=style)
    final = edit(co, 'view', style=style)
    other = Company(root).run('pos-box list')
    assert other == final and other['home_view_style'] == style
    boxes = {b['id']: b for b in other['items']}
    assert boxes[one]['view_style'] == style and boxes[two]['view_style'] == 'block'
    assert co.run('company show')['info_version'] == settings_version


def test_stale_layout_revision_rejects_all_mutations_and_dry_run_does_not_seed(co):
    first = co.run('pos-box list')
    trial = co.run('pos-box seed', dict(mode='empty', expected_version=0), dry_run=True)
    assert trial['items'] == []
    assert co.run('pos-box list') == first
    edit(co, 'seed', mode='empty')
    box = group(co, 'One')
    version = co.run('pos-box list')['version']
    edit(co, 'view', style='photo')
    for verb, body in (
        ('create', dict(kind='group', label='Other')), ('update', dict(pos_box=box, label='Wrong')),
        ('move', dict(pos_box=box, position=0)), ('remove', dict(pos_box=box)),
        ('view', dict(style='list')), ('seed', dict(mode='empty'))):
        assert co.fails('pos-box ' + verb, dict(body, expected_version=version)).code == 'E_VERSION_CONFLICT'
    assert co.fails('pos-box seed', dict(mode='empty', expected_version=version + 1)).code == 'E_VALIDATION'


def test_concurrent_edits_have_one_winner(co, root):
    edit(co, 'seed', mode='empty')
    version = co.run('pos-box list')['version']
    def create(label):
        other = Company(root)
        try:
            return other.run('pos-box create', dict(kind='group', label=label, expected_version=version))
        except bookflow.BookflowError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(create, ['One', 'Two']))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert next(r for r in results if isinstance(r, str)) in ('E_VERSION_CONFLICT', 'E_DB_BUSY')
    assert len(co.run('pos-box list')['items']) == 1


def test_group_picture_validated_read_scoped_and_retained_until_cleared(co):
    edit(co, 'seed', mode='empty')
    key = group(co, 'Photo')
    data = png(4, 4)
    raw = dict(pos_box=key, expected_version=co.run('pos-box list')['version'], original_filename='group.png')
    picture = co.c.run('pos-box picture set', raw, company=co.cid, reason='test group picture',
                       input_stream=io.BytesIO(data))
    shown = next(b['picture'] for b in picture['items'] if b['id'] == key)
    assert shown['media_type'] == 'image/png'
    stream = io.BytesIO()
    co.c.run('pos-box picture get', {'attachment': shown['attachment_id']}, company=co.cid, output_stream=stream)
    assert stream.getvalue() == data
    # No generic attachment owner link is needed: chosen group pointers themselves keep bodies alive.
    co.run('company compact')
    stream = io.BytesIO()
    co.c.run('pos-box picture get', {'attachment': shown['attachment_id']}, company=co.cid, output_stream=stream)
    assert stream.getvalue() == data
    raw['expected_version'] = picture['version']
    with pytest.raises(bookflow.BookflowError) as invalid:
        co.c.run('pos-box picture set', raw, company=co.cid, reason='invalid picture', input_stream=io.BytesIO(b'%PDF-1.7'))
    assert invalid.value.code == 'E_VALIDATION'
    edit(co, 'update', pos_box=key, picture_attachment=None)
    with pytest.raises(bookflow.BookflowError) as missing:
        co.c.run('pos-box picture get', {'attachment': shown['attachment_id']}, company=co.cid, output_stream=io.BytesIO())
    assert missing.value.code == 'E_RECORD_NOT_FOUND'
    assert co.run('company compact')['collected_count'] >= 1
