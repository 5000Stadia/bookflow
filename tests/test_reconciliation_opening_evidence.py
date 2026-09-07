"""Retained citation identity; no runtime audience or activation substitute."""
import copy
import inspect
import io
import itertools
import sqlite3

import pytest

from bookflow.company.reconciliation_storage_validation import InvalidStorage, validate
from tests.test_deposit_lifecycle import driver
from tests.test_reconciliation_storage_validation import (
    account, journal, pair, adapters, references, aggregate, captured, insert, COMPANY,
)


def test_ordinary_transaction_attachment_and_independent_owner_denials(client, driver):
    bank=account(client,'Evidence bank'); equity=account(client,'Evidence equity','equity')
    first=journal(client,pair(bank,equity,'10'))
    second=journal(client,pair(bank,equity,'2'))
    added=client.attachment.add(record_type='transaction',record_id=first['id'],
        original_filename='evidence.bin',input_stream=io.BytesIO(b'ordinary evidence\0\xff'),company=COMPANY)
    linked=client.attachment.link(attachment=added['attachment']['id'],record_type='transaction',
        record_id=second['id'],company=COMPANY)
    other=client.attachment.add(record_type='transaction',record_id=first['id'],
        original_filename='other.bin',input_stream=io.BytesIO(b'different bytes'),company=COMPANY)
    with driver.session() as s:
        g=adapters.graph(s,[first['id']]); refs=references(s)
    rows=aggregate(captured(g),g,bank)
    captures={v['id']:g for name in ('openings','certificates') for v in rows[name]}
    evidence=dict(opening_id=rows['openings'][0]['id'],ordinal=0,kind='transaction_attachment',
        transaction_id=first['id'],attachment_id=added['attachment']['id'],
        attachment_link_id=added['link']['id'],captured_evidence='{}')
    rows['opening_evidence']=[evidence,dict(evidence,ordinal=1,kind='transaction',attachment_id=None,attachment_link_id=None)]
    assert 'audience' not in inspect.signature(validate).parameters
    def check(values, old=refs):
        validate(values,source=g,captured_graphs=captures,referenced_rows=old)
    check(rows)
    for change in (dict(attachment_link_id=linked['link']['id']),dict(attachment_id=other['attachment']['id'])):
        bad=copy.deepcopy(rows);bad['opening_evidence'][0].update(change)
        with pytest.raises(InvalidStorage,match='attachment_owner'):check(bad)
    # Malformed retained-reference seam only: ordinary records.resolve cannot
    # create a customer link to a transaction-only ID. No company store mutation.
    wrong_type=copy.deepcopy(refs)
    next(v for v in wrong_type['attachment_links'] if v['id']==added['link']['id'])['record_type']='customer'
    with pytest.raises(InvalidStorage,match='attachment_owner'):check(rows,wrong_type)
    shapes=[]
    for kind,tx,attachment,link in itertools.product(
            ('transaction','transaction_attachment','opening_attachment',None),(None,first['id']),
            (None,added['attachment']['id']),(None,added['link']['id'])):
        if (kind,tx,attachment,link) in (
                ('transaction',first['id'],None,None),
                ('transaction_attachment',first['id'],added['attachment']['id'],added['link']['id'])):continue
        value=dict(evidence,kind=kind,transaction_id=tx,attachment_id=attachment,attachment_link_id=link)
        shapes.append(value)
        bad=copy.deepcopy(rows);bad['opening_evidence']=[value]
        with pytest.raises(InvalidStorage):check(bad)
    assert len(shapes)==30
    from bookflow.core.ids import new_id
    absent=dict(evidence,kind='transaction',transaction_id=new_id(),attachment_id=None,attachment_link_id=None)
    bad=copy.deepcopy(rows);bad['opening_evidence']=[absent]
    with pytest.raises(InvalidStorage,match='foreign_owner'):check(bad)
    order=['keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','event_effects','draft_revisions','drafts','openings','certificates','certificate_members','event_accounts','accounts','active_certificates','claims','current_members','operation_items','operation_accounts','operation_transactions','operation_drafts','operation_openings','operation_certificates','opening_evidence']
    with driver.session() as s:
        raw=s.company.raw
        for name in order:insert(raw,name,rows[name])
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        for change in (dict(attachment_link_id=linked['link']['id']),dict(attachment_id=other['attachment']['id'])):
            with pytest.raises(sqlite3.IntegrityError,match='invalid reconciliation storage transition'):
                insert(raw,'opening_evidence',[dict(evidence,ordinal=2,**change)])
        for value in shapes:
            with pytest.raises(sqlite3.IntegrityError):insert(raw,'opening_evidence',[dict(value,ordinal=2)])
        with pytest.raises(sqlite3.IntegrityError,match='FOREIGN KEY'):
            insert(raw,'opening_evidence',[dict(absent,ordinal=2)])
        assert raw.execute('SELECT count(*) FROM reconciliation_opening_evidence').fetchone()==(2,)
        assert adapters.graph(s,[first['id']]).rows==g.rows
    # Exercise same-ID/wrong-type against the shipped SQL in a tiny relational
    # seam, not a falsified ordinary-command world. All real evidence FKs stay ON.
    from importlib import import_module
    migration=import_module('bookflow.storage.company_migrations.versions.0022_reconciliation_storage')
    with sqlite3.connect(':memory:') as raw:
        raw.execute('PRAGMA foreign_keys=ON')
        for name in ('reconciliation_openings','transactions','attachments'):
            raw.execute(f'CREATE TABLE {name}(id TEXT PRIMARY KEY)')
        raw.execute('CREATE TABLE attachment_links(id TEXT PRIMARY KEY, attachment_id TEXT, record_type TEXT, record_id TEXT)')
        raw.execute(next(v for v in migration.DDL if v.startswith('CREATE TABLE reconciliation_opening_evidence ')))
        raw.execute(next(v for v in migration.GUARDS if 'opening_evidence_attachment_owner' in v))
        for name,key in (('reconciliation_openings',evidence['opening_id']),('transactions',first['id']),('attachments',evidence['attachment_id'])):
            raw.execute(f'INSERT INTO {name} VALUES (?)',(key,))
        raw.execute('INSERT INTO attachment_links VALUES (?,?,?,?)',(evidence['attachment_link_id'],evidence['attachment_id'],'customer',first['id']))
        with pytest.raises(sqlite3.IntegrityError,match='invalid reconciliation storage transition'):insert(raw,'opening_evidence',[evidence])
        raw.execute("UPDATE attachment_links SET record_type='transaction'")
        insert(raw,'opening_evidence',[evidence])
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
