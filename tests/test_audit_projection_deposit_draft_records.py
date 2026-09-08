"""Actual immutable co24 audit rows; no decoder registration or policy provider."""
import copy
import json
import shutil

import bookflow
import pytest
from pydantic import BaseModel
from bookflow.core.audit import decode_snapshot as stored_decode
from bookflow.core.errors import BookflowError
from bookflow.company import schema, deposit_drafts, deposit_selection
from bookflow.hub import audit_projection_deposit_drafts as codec
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_deposit_drafts import cash
from tests.test_deposit_draft_financial import run_private, financial


@pytest.fixture(scope='module')
def records(_seeded_template, tmp_path_factory):
    root = tmp_path_factory.mktemp('draft-codec') / 'root'
    shutil.copytree(_seeded_template, root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT', str(root))
        patch.delenv('BOOKFLOW_COMPANY', raising=False)
        client = bookflow.connect(data_root=str(root))
        sale_data = sale.__wrapped__(client)
        source = cash.__wrapped__(client, sale_data)
        run = run_private.__wrapped__(client, patch)
        def command(owner, verb, **body):
            return run(lambda s, ctx: owner.run(s, ctx, owner.INPUTS[verb].model_validate(body), verb))
        bank = client.account.create(name='Codec bank', type='bank', company=COMPANY)['id']
        vendor = client.vendor.create(name='Codec vendor', company=COMPANY)['id']
        till = client.account.create(name='Codec till', type='other_current_asset', company=COMPANY)['id']
        draft = command(deposit_drafts, 'create', header=dict(deposit_to=bank, date='2026-06-03',
            cash_back=dict(account=till, amount='1.00', memo='Codec cash back')))
        draft = command(deposit_drafts, 'update', draft=draft.id, expected_version=draft.version,
            set_sources=[source], set_additional=[dict(received_from=dict(kind='vendor', id=vendor),
                from_account=sale_data['income'], amount='1.25', memo='Extra')])
        selection = command(deposit_selection, 'create', draft=draft.id, expected_version=draft.version)
        selection = command(deposit_selection, 'update', selection=selection.id, expected_version=selection.version,
            set_sources=[dict(source, memo_override='Selected memo')])
        selection = command(deposit_selection, 'clear', selection=selection.id, expected_version=selection.version)
        selection = command(deposit_selection, 'update', selection=selection.id, expected_version=selection.version, set_sources=[source])
        accepted = command(deposit_selection, 'accept', selection=selection.id, expected_version=selection.version,
            draft=draft.id, expected_draft_version=draft.version)
        draft = accepted.draft
        posted = financial(run, dict(operation_key='codec-consume', document=dict(mode='draft', draft=draft.id, expected_version=draft.version)))
        assert posted.current.revision_bank_total == 6025
        assert posted.effect.financial.cash_back == 100
        command(deposit_drafts, 'create', from_deposit=posted.current.id, expected_version=posted.current.version)
        def capture(s, ctx):
            baseline = tuple(s.company.raw.iterdump())
            cursor = s.company.raw.execute('SELECT a.command,e.* FROM audit_entries e JOIN audit_events a ON a.id=e.event_id ORDER BY a.seq,e.id')
            rows = [dict(zip((c[0] for c in cursor.description), row, strict=True)) for row in cursor.fetchall()]
            rows = [row for row in rows if row['record_type'] in codec.MODELS]
            # Decode while the real owner's database remains open; it must not write.
            for row in rows:
                for side in ('before', 'after'):
                    if row[side] is not None:
                        codec.decode_snapshot(producer=row['command'], record_type=row['record_type'], action=row['action'],
                            record_id=row['record_id'], snapshot=stored_decode(row[side]))
            assert tuple(s.company.raw.iterdump()) == baseline
            return rows
        rows = run(capture)
        assert {r['record_type'] for r in rows} == set(codec.MODELS)
        return rows


def assert_capture(actual, raw):
    """All raw fields checked, even fields excluded from public serialization."""
    if isinstance(actual, BaseModel):
        if type(raw) is str:
            raw = json.loads(raw)
        # Existing legacy Origins deliberately transforms maps to ordered values.
        if type(actual).__name__ == 'Origins':
            assert actual.model_dump(mode='json') == {'values': [dict(field=k, origin=v) for k, v in sorted(raw.items())]}
            return
        for key, value in raw.items():
            name = next((n for n, f in type(actual).model_fields.items() if (f.alias or n) == key), None)
            assert name is not None, key
            assert_capture(getattr(actual, name), value)
    elif isinstance(actual, (list, tuple)):
        assert len(actual) == len(raw)
        for a, b in zip(actual, raw, strict=True):
            assert_capture(a, b)
    elif isinstance(actual, dict):
        assert set(actual) == set(raw)
        for key in raw:
            assert_capture(actual[key], raw[key])
    else:
        assert actual == raw


@pytest.mark.parametrize('kind', tuple(codec.MODELS))
def test_actual_before_after_all_fields_and_finite_schema(records, kind):
    model = codec.MODELS[kind]
    table, keys = codec.TABLES[kind]
    assert set(model.model_fields) - {'projection_partial', 'tag'} == set(getattr(schema, table).columns.keys())
    assert tuple(c.name for c in getattr(schema, table).primary_key.columns) == keys
    selected = [row for row in records if row['record_type'] == kind]
    assert selected
    for row in selected:
        for side in ('before', 'after'):
            if row[side] is None:
                continue
            raw = stored_decode(row[side]); original = copy.deepcopy(raw)
            value = codec.decode_snapshot(producer=row['command'], record_type=kind, action=row['action'],
                                          snapshot=raw, record_id=row['record_id'])
            assert_capture(value, raw)
            assert raw == original
            public = value.model_dump(mode='json')
            assert public['tag'] == kind
            assert not (value._internal & public.keys())
            if 'snapshot' in raw:
                assert isinstance(public['snapshot'], dict)
    if kind in ('deposit_draft', 'deposit_selection'):
        assert any(r['before'] is not None for r in selected)


def test_actual_branches_and_consumed_head(records):
    assert {'deposit draft create', 'deposit draft update', 'deposit selection create', 'deposit selection update',
            'deposit selection clear', 'deposit selection accept', 'deposit post'} <= {r['command'] for r in records}
    consumed = next(stored_decode(r['after']) for r in records if r['record_type'] == 'deposit_draft' and r['command'] == 'deposit post')
    receipt = next(stored_decode(r['after']) for r in records if r['record_type'] == 'deposit_draft_consumption')
    assert consumed['state'] == 'consumed'
    assert consumed['current_revision_id'] == consumed['consumed_revision_id'] == receipt['revision_id']
    assert consumed['consumed_operation_id'] == receipt['operation_id']
    sources = [r for r in records if r['record_type'] == 'deposit_draft_source']
    assert len({r['record_id'] for r in sources}) < len(sources)  # immutable row ID reused in later revision
    assert len({(r['record_id'], stored_decode(r['after'])['revision_id']) for r in sources}) == len(sources)


@pytest.mark.parametrize('fault', ['state', 'consumed_head', 'copy_pair', 'ordinal', 'source_identity', 'source_version',
    'additional_party', 'additional_amount', 'manifest_hash', 'high_water', 'bank', 'previous', 'accepted', 'unknown', 'plain_json', 'record_id', 'command'])
def test_malformed_capture_rejected(records, fault):
    kind = {'state':'deposit_draft','consumed_head':'deposit_draft','copy_pair':'deposit_draft',
        'ordinal':'deposit_draft_row_key','source_identity':'deposit_draft_source','source_version':'deposit_draft_source',
        'additional_party':'deposit_draft_additional','additional_amount':'deposit_draft_additional',
        'manifest_hash':'deposit_draft_revision','high_water':'deposit_draft_revision','bank':'deposit_draft_revision',
        'previous':'deposit_draft_revision','accepted':'deposit_selection','unknown':'deposit_draft_consumption',
        'plain_json':'deposit_draft_source','record_id':'deposit_draft_source','command':'deposit_draft'}[fault]
    row = next(r for r in reversed(records) if r['record_type'] == kind
               and (fault != 'previous' or stored_decode(r['after'])['version'] > 1))
    raw = stored_decode(row['after']); record_id = row['record_id']; producer = row['command']
    if fault == 'state': raw['state'] = 'posted'
    elif fault == 'consumed_head': raw['consumed_revision_id'] = raw['id']
    elif fault == 'copy_pair': raw['copy_type'] = 'deposit'
    elif fault == 'ordinal': raw['ordinal'] = True
    elif fault == 'source_identity': raw['source_transaction_id'] = raw['row_id']
    elif fault == 'source_version': raw['expected_header_version'] += 1
    elif fault == 'additional_party': raw['customer_id'] = raw['vendor_id']
    elif fault == 'additional_amount': raw['amount_minor_units'] += 1
    elif fault == 'manifest_hash': raw['manifest_hash'] = '0' * 64
    elif fault == 'high_water': raw['high_water'] += 1
    elif fault == 'bank': raw['bank_account_id'] = raw['id']
    elif fault == 'previous': raw['previous_revision_id'] = None
    elif fault == 'accepted': raw['accepted_revision_id'] = None
    elif fault == 'unknown': raw['invented'] = True
    elif fault == 'plain_json': raw['snapshot'] = '[]'
    elif fault == 'record_id': record_id = raw['revision_id']
    else: producer = 'deposit void'
    with pytest.raises(BookflowError) as caught:
        codec.decode_snapshot(producer=producer, record_type=kind, action=row['action'], record_id=record_id, snapshot=raw)
    assert caught.value.code == 'E_VALIDATION' and caught.value.details == {'reason':'audit_format'}


def test_missing_optional_source_capture_and_projection_null(records):
    row = next(r for r in records if r['record_type'] == 'deposit_draft_source')
    raw = stored_decode(row['after']); snapshot = json.loads(raw['snapshot'])
    snapshot.pop('captured_header_version', None); raw['snapshot'] = json.dumps(snapshot)
    value = codec.decode_snapshot(producer=row['command'], record_type=row['record_type'], action=row['action'], snapshot=raw)
    assert 'captured_header_version' not in value.snapshot.model_dump(mode='json')
    masked = value.model_copy(update={'source_transaction_id':None,'source_type':None})
    assert masked.model_dump(mode='json')['source_transaction_id'] is None
    raw['source_transaction_id'] = None
    with pytest.raises(BookflowError):
        codec.decode_snapshot(producer=row['command'], record_type=row['record_type'], action=row['action'], snapshot=raw)


def test_literal_nine_owner_names_and_route_fields():
    assert set(codec.MODELS) == {'deposit_draft','deposit_selection','deposit_draft_revision','deposit_selection_revision',
        'deposit_draft_row_key','deposit_draft_source','deposit_selection_source','deposit_draft_additional','deposit_draft_consumption'}
    assert set(codec.TABLES) == set(codec.PRODUCERS) == set(codec.MODELS)
    for model, groups in codec.REFERENCE_GROUPS.items():
        for fields, _ in groups:
            for field in fields:
                assert field in model.model_fields
                assert type(None) in __import__('typing').get_args(model.model_fields[field].annotation)


# Exact fields written to None by the existing disclosure walker's identity and
# partial-provenance paths; these are still nonnull in raw co24 captures.
CREATED_PROVENANCE = frozenset({'created_at', 'created_by', 'created_via'})
HEADER_PROVENANCE = frozenset({'version', 'updated_at', 'updated_by', 'updated_via'})


def provenance_fields(kind):
    if kind in ('deposit_draft', 'deposit_selection'):
        return CREATED_PROVENANCE | HEADER_PROVENANCE
    if kind in ('deposit_draft_revision', 'deposit_selection_revision'):
        return CREATED_PROVENANCE | {'version'}
    return CREATED_PROVENANCE


@pytest.mark.parametrize('kind', tuple(codec.MODELS))
def test_raw_null_provenance_rejected_for_every_record_family(records, kind):
    row = next(r for r in records if r['record_type'] == kind)
    raw = stored_decode(row['after'])
    for field in sorted(provenance_fields(kind)):
        assert raw[field] is not None
        damaged = copy.deepcopy(raw)
        damaged[field] = None
        with pytest.raises(BookflowError) as caught:
            codec.decode_snapshot(producer=row['command'], record_type=kind,
                                  action=row['action'], snapshot=damaged)
        assert caught.value.code == 'E_VALIDATION'
        assert caught.value.details == {'reason': 'audit_format'}


@pytest.mark.parametrize('kind', tuple(codec.MODELS))
def test_trusted_identity_and_partial_provenance_nulls_serialize(records, kind):
    from typing import get_args
    from bookflow.hub import audit_projection_legacy as legacy
    row = next(r for r in records if r['record_type'] == kind)
    raw = stored_decode(row['after'])
    value = codec.decode_snapshot(producer=row['command'], record_type=kind,
                                  action=row['action'], snapshot=raw)
    fields = provenance_fields(kind)
    assert fields == legacy._PARTIAL_PROVENANCE & codec.MODELS[kind].model_fields.keys()
    for field in fields:
        assert type(None) in get_args(codec.MODELS[kind].model_fields[field].annotation)
    # Exercise created and updated identity denial independently before the
    # partial path strips them, including the serializer's intermediate handler.
    for field in sorted(fields & {'created_by', 'updated_by'}):
        masked = value.model_copy(update={field: None})
        expected = value.model_dump(mode='json', warnings='error')
        expected[field] = None
        assert masked.model_dump(mode='json', warnings='error') == expected
        assert json.loads(masked.model_dump_json(warnings='error')) == expected
    partial = value.model_copy(update={**dict.fromkeys(fields), 'projection_partial': True})
    expected = {k: v for k, v in value.model_dump(mode='json').items() if k not in fields}
    assert partial.model_dump(mode='json', warnings='error') == expected
    assert json.loads(partial.model_dump_json(warnings='error')) == expected
    assert_capture(value, raw)  # trusted copies never rewrite the original capture


# Faults change only detached captured dictionaries, never the database. Every
# original is decoded first, so malformed setup cannot masquerade as rejection.
def decode_record(row, raw):
    return codec.decode_snapshot(producer=row['command'], record_type=row['record_type'],
        action=row['action'], record_id=row['record_id'], snapshot=raw)


def assert_format_rejected(row, raw):
    with pytest.raises(BookflowError) as caught:
        decode_record(row, raw)
    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details == {'reason': 'audit_format'}


@pytest.mark.parametrize('kind', ('deposit_draft_source', 'deposit_selection_source'))
def test_source_memo_agreement_is_independent_of_row_snapshot_agreement(records, kind):
    row = next(r for r in records if r['record_type'] == kind
               and stored_decode(r['after'])['memo_origin'] == 'source')
    raw = stored_decode(row['after'])
    original = decode_record(row, raw)
    snapshot = json.loads(raw['snapshot'])
    assert raw['memo'] == snapshot['memo'] == snapshot['source']['source_memo']
    raw['memo'] = snapshot['memo'] = 'Contradictory captured source memo'
    assert snapshot['memo'] != snapshot['source']['source_memo']
    raw['snapshot'] = json.dumps(snapshot)
    assert_format_rejected(row, raw)
    # The same unequal memo is legitimate when explicitly entered on both sides.
    raw['memo_origin'] = snapshot['memo_origin'] = 'entered'
    raw['snapshot'] = json.dumps(snapshot)
    assert decode_record(row, raw).memo == 'Contradictory captured source memo'
    assert original.memo_origin == 'source'


def test_selection_revision_rejects_valid_additional_manifest(records):
    from bookflow.company.deposit_draft_models import Manifest, Additional
    from bookflow.company.deposit_draft_validation import validate_manifest
    from bookflow.company.payment_queries import digest
    row = next(r for r in records if r['record_type'] == 'deposit_selection_revision'
               and json.loads(stored_decode(r['after'])['snapshot'])['sources'])
    raw = stored_decode(row['after'])
    decode_record(row, raw)
    previous = Manifest.model_validate_json(raw['snapshot'])
    additional_row = next(r for r in records if r['record_type'] == 'deposit_draft_additional')
    additional = Additional.model_validate_json(stored_decode(additional_row['after'])['snapshot'])
    # Retain real row identity/source captures; use a fresh noncolliding ordinal.
    additional = additional.model_copy(update={'ordinal': previous.high_water + 1})
    proposed = deposit_drafts.manifest(previous.currency, previous.header, previous.sources,
        (additional,), previous.high_water + 1)
    validate_manifest(proposed)
    assert proposed.summary.additional_count == 1
    assert proposed.summary.known_additional_total == 125
    assert proposed.summary.source_total == 6000
    raw.update(snapshot=proposed.model_dump_json(), high_water=proposed.high_water,
               manifest_hash=digest(proposed.model_dump(mode='json')))
    # Prove every inherited revision guard succeeds; only selection forbids rows.
    inherited = {key: value for key, value in raw.items() if key in codec.Revision.model_fields}
    assert codec.Revision.model_validate(inherited).snapshot.additional[0].units == 125
    assert_format_rejected(row, raw)


def test_draft_revision_cashback_account_matches_actual_capture(records):
    row = next(r for r in records if r['record_type'] == 'deposit_draft_revision'
               and stored_decode(r['after'])['cashback_account_id'] is not None)
    raw = stored_decode(row['after'])
    original = decode_record(row, raw)
    assert original.snapshot.header.cash_back.units == 100
    assert raw['cashback_account_id'] == original.snapshot.header.cash_back.account.id
    assert raw['bank_account_id'] != raw['cashback_account_id']
    raw['cashback_account_id'] = raw['bank_account_id']
    # Manifest/hash/bank counterpart and all other fields remain untouched.
    assert_format_rejected(row, raw)


@pytest.mark.parametrize('missing', ('edit_transaction_id', 'original_row_id'))
def test_real_edit_row_key_requires_both_origin_fields(records, missing):
    row = next(r for r in records if r['record_type'] == 'deposit_draft_row_key'
               and stored_decode(r['after'])['edit_transaction_id'] is not None)
    raw = stored_decode(row['after'])
    value = decode_record(row, raw)
    header = next(stored_decode(r['after']) for r in records if r['record_type'] == 'deposit_draft'
                  and stored_decode(r['after'])['id'] == value.draft_id)
    assert header['edit_transaction_id'] == value.edit_transaction_id
    assert header['copy_transaction_id'] is None
    assert value.original_row_id is not None
    assert any(r['record_type'] == 'deposit_draft_consumption' for r in records)
    raw[missing] = None
    assert_format_rejected(row, raw)


def test_descriptor_kinds_resolve_and_owner_edges_name_real_fields():
    from bookflow.hub import audit_projection_legacy as legacy
    from typing import get_args
    kinds = {kind for groups in codec.REFERENCE_GROUPS.values() for _, kind in groups}
    for model, (discriminator, identity) in codec.SOURCE_ROUTES.items():
        assert identity in model.model_fields
        kinds.update(kind for arg in get_args(model.model_fields[discriminator].annotation)
                     for kind in get_args(arg))
    for model, (discriminator, pairs) in codec.PARTY_ROUTES.items():
        assert discriminator in model.model_fields
        for kind, identity in pairs:
            kinds.add(kind)
            assert identity in model.model_fields
    assert kinds == {'account', 'class', 'customer', 'deposit', 'employee', 'other_name',
                     'payment', 'payment_method', 'sales_receipt', 'vendor'}
    for kind in kinds:
        assert legacy.entry_requirement(kind)
    external = {'deposit_operation': legacy.DepositOperationView}
    for model, edges in codec.OWNER_EDGES.items():
        assert model in codec.MODELS.values()
        for field, target in edges:
            assert field in model.model_fields
            target_model = (codec.MODELS | external)[target]
            assert target_model.model_fields['tag'].default == target
