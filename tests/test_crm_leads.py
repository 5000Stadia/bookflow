"""R115 increment 1: prospects stay separate from debtors and native work keeps target authority."""
from pathlib import Path
import io
import sqlite3

import anyio
import pytest

from bookflow import BookflowError

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset('lead ' + verb for verb in ('create', 'update', 'show', 'list', 'close', 'reopen', 'archive'))


@pytest.fixture
def books(tmp_path, monkeypatch):
    from tests.crm_support import company
    return company(tmp_path, monkeypatch, 'UTC')


def write(books, command, values, **context):
    return books.run(command, values, reason='Record the enquiry', **context)


def test_name_only_lead_native_note_and_follow_up_with_optional_layers_off(books):
    customers = books.run('customer list', {})['items']
    lead = write(books, 'lead create', {'display_name': 'Morning enquiry'})
    assert lead['display_name'] == 'Morning enquiry' and lead['status'] == 'open'
    assert lead['rating'] == 'unrated' and not lead['archived']
    note = write(books, 'note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'Asked about repairs'})['note']
    task = write(books, 'todo create', {'title': 'Return the call', 'due_date': '2099-01-04', 'link_type': 'lead', 'link': lead['id']})
    assert note['author_id'] == lead['created_by'] and note['interface'] == 'python'
    assert books.run('note show', {'note': note['id']})['body'] == 'Asked about repairs'
    assert books.run('lead show', {'lead': lead['id']})['next_follow_up']['id'] == task['id']
    due = books.run('reminders', {'as_of': '2099-01-04'})
    assert [t['id'] for t in due['todos_due']].count(task['id']) == 1
    assert books.run('customer list', {})['items'] == customers
    path = Path(books.run('company show', {})['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT crm_pipeline_enabled,crm_campaigns_enabled FROM company_info').fetchone() == (0, 0)
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('crm_pipelines','crm_campaigns','crm_opportunities')").fetchall()


def test_lead_input_validation_rejects_invalid_contact_and_unversioned_edit():
    from pydantic import ValidationError
    from bookflow.company.crm_models import LeadCreateInput, LeadUpdateInput
    assert LeadCreateInput(display_name='Name only').contacts == []
    for payload in ({'display_name': ' '}, {'display_name': 'Name', 'rating': 'urgent'},
                    {'display_name': 'Name', 'contacts': [{'role': 'invalid'}]},
                    {'display_name': 'Name', 'contacts': [{'role': 'primary', 'points': [{'kind': 'unknown', 'value': 'x'}]}]},
                    {'display_name': 'Name', 'contacts': [{'role': 'primary', 'points': [{'kind': 'main_email', 'value': ''}]}]}):
        with pytest.raises(ValidationError):
            LeadCreateInput.model_validate(payload)
    for version in (None, True, '1', 0):
        with pytest.raises(ValidationError):
            LeadUpdateInput.model_validate({'lead': 'Name', 'expected_version': version, 'rating': 'warm'})


def test_contacts_validate_and_keep_stable_identity(books):
    for contacts in ([{'role': 'primary'}, {'role': 'primary'}], [{'role': 'invalid'}],
                     [{'role': 'primary', 'display_name': ' '}],
                     [{'role': 'primary', 'points': [{'kind': 'main_email', 'value': ''}]}]):
        with pytest.raises(BookflowError) as refused:
            write(books, 'lead create', {'display_name': 'Invalid contacts', 'contacts': contacts})
        assert refused.value.code == 'E_VALIDATION'
    lead = write(books, 'lead create', {'display_name': 'Workshop enquiry', 'contacts': [
        {'role': 'primary', 'display_name': 'Sam', 'primary_email': 'Sam@Example.test',
         'points': [{'kind': 'mobile_phone', 'value': '+1 (555) 010-2345'}]}]})
    contact = lead['contacts'][0]
    assert contact['primary_email'] == 'Sam@Example.test'
    updated = write(books, 'lead update', {'lead': lead['id'], 'expected_version': 1,
        'contacts': [{'id': contact['id'], 'role': 'primary', 'display_name': 'Sam R', 'primary_email': 'Sam@Example.test'}]})
    assert updated['contacts'][0]['id'] == contact['id']
    assert updated['contacts'][0]['points'][0]['value'] == '+1 (555) 010-2345'
    other = write(books, 'lead create', {'display_name': 'Other enquiry'})
    with pytest.raises(BookflowError) as refused:
        write(books, 'lead update', {'lead': other['id'], 'expected_version': 1,
            'contacts': [{'id': contact['id'], 'role': 'primary', 'display_name': 'Borrowed'}]})
    assert refused.value.code == 'E_RECORD_NOT_FOUND'


def test_duplicate_names_need_ids_and_versioned_lifecycle_preserves_tasks(books):
    first = write(books, 'lead create', {'display_name': 'Alex Reed'})
    second = write(books, 'lead create', {'display_name': 'Alex Reed'})
    with pytest.raises(BookflowError) as ambiguous:
        books.run('lead show', {'lead': 'Alex Reed'})
    assert ambiguous.value.code == 'E_AMBIGUOUS_SELECTOR'
    assert first['id'] != second['id']
    todo = write(books, 'todo create', {'title': 'Call Alex', 'link_type': 'lead', 'link': first['id']})
    with pytest.raises(BookflowError) as missing:
        books.run('lead close', {'lead': first['id'], 'expected_version': 1})
    assert missing.value.code in ('E_REASON_REQUIRED', 'E_VALIDATION')
    closed = write(books, 'lead close', {'lead': first['id'], 'expected_version': 1, 'duplicate_of': second['id']})
    assert closed['status'] == 'closed'
    with pytest.raises(BookflowError) as stale:
        write(books, 'lead reopen', {'lead': first['id'], 'expected_version': 1})
    assert stale.value.code == 'E_VERSION_CONFLICT'
    assert 'status' in stale.value.details['changed_fields']
    assert stale.value.details['updated_by'] == closed['updated_by']
    assert stale.value.details['seconds_since_update'] is not None
    reopened = write(books, 'lead reopen', {'lead': first['id'], 'expected_version': closed['version']})
    archived = write(books, 'lead archive', {'lead': first['id'], 'expected_version': reopened['version']})
    assert archived['archived'] and archived['status'] == 'open'
    assert books.run('todo show', {'todo': todo['id']})['status'] == 'open'
    assert first['id'] not in {x['id'] for x in books.run('lead list', {})['items']}
    assert first['id'] in {x['id'] for x in books.run('lead list', {'include_archived': True})['items']}


def test_lead_owner_requires_current_company_membership(books):
    from tests.conftest import make_actor
    outsider = make_actor(books.root, 'outside-owner')
    with pytest.raises(BookflowError) as refused:
        write(books, 'lead create', {'display_name': 'Outside owner', 'owner': outsider})
    assert refused.value.code == 'E_VALIDATION'
    person = make_actor(books.root, 'lead-owner', company_role=(books.company, 'readonly'))
    owned = write(books, 'lead create', {'display_name': 'Owned enquiry', 'owner': person})
    assert owned['owner_id'] == person
    unassigned = write(books, 'lead update', {'lead': owned['id'], 'expected_version': 1, 'owner': None})
    assert unassigned['owner_id'] is None


def test_lead_retry_and_cursor_scope(books):
    first = write(books, 'lead create', {'display_name': 'First'}, idempotency_key='enquiry-1')
    replay = write(books, 'lead create', {'display_name': 'First'}, idempotency_key='enquiry-1')
    assert replay['id'] == first['id']
    with pytest.raises(BookflowError) as mismatch:
        write(books, 'lead create', {'display_name': 'Changed'}, idempotency_key='enquiry-1')
    assert mismatch.value.code == 'E_IDEMPOTENCY_MISMATCH'
    second = write(books, 'lead create', {'display_name': 'Second'})
    page = books.run('lead list', {'limit': 1})
    assert page['count'] == 1 and page['total_count'] == 2 and page['has_more']
    last = books.run('lead list', {'limit': 1, 'cursor': page['next_cursor']})
    assert {page['items'][0]['id'], last['items'][0]['id']} == {first['id'], second['id']}
    assert not last['has_more']
    with pytest.raises(BookflowError) as scope:
        books.run('lead list', {'limit': 1, 'cursor': page['next_cursor'], 'search': 'Second'})
    assert scope.value.code == 'E_VALIDATION'


@pytest.mark.parametrize('source', ('lead', 'note', 'attachment', 'todo'))
def test_lead_target_denial_covers_note_attachment_and_task(books, source):
    from tests.conftest import as_user, make_actor
    from bookflow.core import registry
    lead = write(books, 'lead create', {'display_name': 'Private prospect'})
    if source == 'note':
        note = write(books, 'note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'Private note'})['note']
    if source == 'todo':
        task = write(books, 'todo create', {'title': 'Private task', 'link_type': 'lead', 'link': lead['id']})
    if source == 'attachment':
        attachment = books.client.attachment.add(record_type='lead', record_id=lead['id'], original_filename='enquiry.txt',
            input_stream=io.BytesIO(b'Private evidence'), company=books.company, reason='Keep enquiry evidence')
    user = make_actor(books.root, 'crm-denied', company_role=(books.company, 'standard'))
    books.client.run('membership grant', {'user': user, 'company': books.company, 'role': 'standard', 'denies': ['crm.read'], 'expected_version': 1}, reason='Limit source access')
    denied = as_user(books.root, 'crm-denied')
    cases = []
    if source == 'lead':
        cases = [('lead show', {'lead': lead['id']})]
    elif source == 'note':
        cases = [
            ('note show', {'note': note['id']}),
            ('note show', {'note': note['id'].lower()}),
            ('note edit', {'note': note['id'], 'body': 'Changed', 'expected_version': 1}),
            ('note list', {'record_type': 'lead', 'record_id': lead['id']}),
            ('note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'No access'}),
        ]
    elif source == 'attachment':
        cases = [('attachment list', {'record_type': 'lead', 'record_id': lead['id']})]
    elif source == 'todo':
        cases = [('todo show', {'todo': task['id']}), ('todo complete', {'todo': task['id']})]
    for name, values in cases:
        with pytest.raises(BookflowError) as refused:
            denied.run(name, values, company=books.company, **({'reason': 'Attempt denied target'} if registry.get(name).is_write else {}))
        assert refused.value.code == 'E_PERMISSION', (name, refused.value.to_dict())
    if source == 'todo':
        assert task['id'] not in {x['id'] for x in denied.run('todo list', {}, company=books.company)['items']}
    if source == 'attachment':
        with pytest.raises(BookflowError) as refused:
            denied.attachment.get(attachment=attachment['attachment']['id'].lower(), output_stream=io.BytesIO(), company=books.company)
        assert refused.value.code == 'E_PERMISSION'
        with pytest.raises(BookflowError) as refused:
            denied.attachment.add(record_type='lead', record_id=lead['id'], original_filename='denied.txt',
                input_stream=io.BytesIO(b'No access'), company=books.company, reason='Attempt denied target')
        assert refused.value.code == 'E_PERMISSION'


def test_old_customer_contacts_survive_crm_migration(books):
    import importlib
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import migrate_to_head
    customer = write(books, 'customer create', {'name': 'Existing contact customer', 'contacts': [
        {'role': 'primary', 'display_name': 'Existing person', 'primary_email': 'person@example.test'}]})
    path = Path(books.run('company show', {})['path']) / 'company.db'
    frozen = importlib.import_module('bookflow.storage.company_migrations.versions.0085_scheduling')
    old_ddl = next(ddl for ddl in frozen.DDL if 'CREATE TABLE todos' in ddl)
    with sqlite3.connect(path) as db:
        before = db.execute('SELECT * FROM customers').fetchall()
        contacts = db.execute('SELECT * FROM customer_contacts').fetchall()
        db.execute('PRAGMA foreign_keys=OFF')
        for table in ('crm_lead_contact_points', 'crm_lead_contacts', 'crm_leads'):
            db.execute('DROP TABLE ' + table)
        for column in ('crm_pipeline_enabled', 'crm_campaigns_enabled'):
            db.execute('ALTER TABLE company_info DROP COLUMN ' + column)
        db.execute('DROP TABLE todos')
        db.execute(old_ddl)
        for ddl in frozen.INDEXES:
            if ' ON todos ' in ddl:
                db.execute(ddl)
        db.execute("UPDATE alembic_version SET version_num='co0131'")
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', None) == ('co0131', 'co0134')
        assert db.raw.execute('SELECT * FROM customers').fetchall() == before
        assert db.raw.execute('SELECT * FROM customer_contacts').fetchall() == contacts
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('SELECT count(*) FROM crm_leads').fetchone() == (0,)
    assert books.run('customer show', {'customer': customer['id']})['contacts'][0]['display_name'] == 'Existing person'


@pytest.mark.parametrize('surface', SURFACES)
def test_lead_workflow_through_python_cli_http_and_mcp(books, tmp_path, surface):
    from tests.mcp_matrix_support import Matrix
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(books.root, tmp_path / 'surfaces')
            call = lambda name, values: matrix.call(surface, name, values)
            lead = await call('lead create', {'display_name': 'Surface enquiry'})
            note = await call('note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'Ask for access'})
            todo = await call('todo create', {'title': 'Arrange visit', 'link_type': 'lead', 'link': lead['id'], 'due_date': '2099-02-01'})
            changed = await call('lead update', {'lead': lead['id'], 'expected_version': 1, 'rating': 'warm'})
            shown = await call('lead show', {'lead': lead['id']})
            listed = await call('lead list', {'search': 'Surface enquiry'})
            due = await call('reminders', {'as_of': '2099-02-01'})
            closed = await call('lead close', {'lead': lead['id'], 'expected_version': changed['version']})
            reopened = await call('lead reopen', {'lead': lead['id'], 'expected_version': closed['version']})
            archived = await call('lead archive', {'lead': lead['id'], 'expected_version': reopened['version']})
            assert shown['rating'] == 'warm' and shown['next_follow_up']['id'] == todo['id']
            assert note['note']['record_id'] == lead['id'] and note['note']['interface'] == surface
            assert [x['id'] for x in listed['items']] == [lead['id']]
            assert [x['id'] for x in due['todos_due']].count(todo['id']) == 1
            assert archived['archived'] and archived['status'] == 'open'
        finally:
            await matrix.close()
    anyio.run(witness)


def test_lowercase_annotation_ids_retain_source_admission(monkeypatch):
    from types import SimpleNamespace
    import sqlalchemy as sa
    from bookflow.company import crm, crm_admission, schema
    from bookflow.core.ids import new_id
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        schema.notes.create(conn)
        key = new_id()
        common = dict(version=1, created_at='2026-11-10T00:00:00Z', created_by='actor', created_via='cli',
            updated_at='2026-11-10T00:00:00Z', updated_by='actor', updated_via='cli')
        conn.execute(schema.notes.insert().values(id=key, **common, record_type='lead', record_id=new_id(),
            body='Private enquiry', author_id='actor', interface='cli', at=common['created_at'], kind='comment'))
        def denied(session):
            raise BookflowError('E_PERMISSION')
        monkeypatch.setattr(crm, 'require_read', denied)
        session = SimpleNamespace(company=SimpleNamespace(conn=conn))
        nested = new_id()
        conn.execute(schema.notes.insert().values(id=nested, **common, record_type='note', record_id=key,
            body='Nested private comment', author_id='actor', interface='cli', at=common['created_at'], kind='comment'))
        schema.audit_entries.create(conn)
        audit_id, event_id = new_id(), new_id()
        conn.execute(schema.audit_entries.insert().values(id=audit_id, event_id=event_id,
            record_type='note', record_id=nested, action='create', version_after=1))
        for kind, identifier in (('note', key.lower()), ('note', nested), ('audit_entry', audit_id), ('audit_event', event_id)):
            with pytest.raises(BookflowError, match='E_PERMISSION'):
                crm_admission.authorize_target(session, kind, identifier)
            references = crm_admission.reference_touches(session, kind, identifier)
            assert len(references) == 1 and references[0].record_type == 'lead'

    engine.dispose()
