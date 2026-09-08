"""Reader redaction is a security signal, distinct from internal-only omission.

These are traversal conformance tests with validated captured views and actual
authenticated permission audiences. No explanation implementation is assumed.
"""
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from bookflow.core.errors import BookflowError
from bookflow.hub import audit_projection as projection
from bookflow.hub import audit_projection_legacy as legacy
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.test_audit_projection_publication import apply
from tests.test_history_field_decisions import audience, hosted, path  # noqa: F401


def deny(hosted, *capabilities):
    host, root = hosted
    apply(host, root, admin.PutMembership(
        'A', ScopeKey('organization', 'O'), admin.Version(1), 'admin',
        denies=capabilities,
    ), 'W')


def profile(**fields):
    return legacy.CommercialProfile(
        customer=legacy.Customer(id='customer-1', label='Captured customer', version=1),
        preferences=legacy.Preferences(
            sales_tax_enabled=False, sales_tax_liability_basis='invoice_date',
            enable_price_levels=False, use_classes=False, prompt_for_class=False,
            units_of_measure_mode='disabled',
        ),
        origins=legacy.Origins(values=()), **fields,
    )


def disclose(hosted, captured, who='A'):
    with audience(hosted, who) as reader:
        return projection._disclose_company(reader, 'C', captured)


@pytest.mark.parametrize('flag', [False, True])
def test_reader_redaction_state_cannot_be_supplied_by_captured_input(flag):
    with pytest.raises(ValidationError):
        legacy.Origin.model_validate({'kind': 'explicit', 'reader_redacted': flag})
    with pytest.raises(ValidationError):
        legacy.Origins.model_validate({
            'terms': {'kind': 'explicit', 'reader_redacted': flag},
        })


def test_reader_redaction_state_is_private_and_defaults_false(hosted):
    captured = legacy.Origin(kind='explicit')
    assert captured.reader_redacted is False
    visible = disclose(hosted, captured)
    assert visible.reader_redacted is False
    assert visible.model_dump(mode='json') == {'kind': 'explicit'}
    # Execution-owned state must stay private even when it is set.
    marked = visible.model_copy(update={'reader_redacted': True})
    assert marked.model_dump(mode='json') == {'kind': 'explicit'}


def test_reference_group_masks_identity_label_and_version_as_one_unit(hosted):
    captured = legacy.Unit(
        id='unit-1', label='Private unit', version=7, set_id='set-1',
        abbreviation='PRIV', factor_nanounits=1_000_000_000,
    )
    allowed = disclose(hosted, captured)
    assert allowed.model_dump() == captured.model_dump()
    assert allowed.reader_redacted is False
    deny(hosted, 'unit_of_measure')
    masked = disclose(hosted, captured)
    assert masked.reader_redacted is True
    assert masked.projection_partial is True
    assert (masked.id, masked.label, masked.version, masked.set_id,
            masked.abbreviation) == (None,) * 5
    assert masked.factor_nanounits == 1_000_000_000
    assert captured.id == 'unit-1' and captured.reader_redacted is False


@pytest.mark.parametrize('presence', ['omitted', 'null', 'populated'])
@pytest.mark.parametrize('field,capability', [
    ('ship_method', 'ship_method'),  # An object reference route.
    ('billing_address', 'customer'),  # A whole-field requirement.
])
def test_optional_capture_absence_is_redacted_too(hosted, presence, field, capability):
    populated = (legacy.Reference(id='ship-1', label='Private carrier', version=4)
                 if field == 'ship_method' else legacy.Address(line1='Private address'))
    fields = {} if presence == 'omitted' else {field: None if presence == 'null' else populated}
    captured = profile(**fields)
    before = captured.model_dump(mode='json')
    allowed = disclose(hosted, captured)
    assert allowed.reader_redacted is False
    assert allowed.model_dump(mode='json') == before
    deny(hosted, capability)
    masked = disclose(hosted, captured)
    assert masked.reader_redacted is True
    assert masked.projection_partial is True
    assert masked.model_dump(mode='json')[field] is None
    assert captured.model_dump(mode='json') == before
    # The entitled reader still sees the original presence, after A's denial.
    assert disclose(hosted, captured, who='W').model_dump(mode='json') == before


@pytest.mark.parametrize('kind', ['customer', 'vendor', 'employee', 'other_name'])
def test_polymorphic_party_masks_discriminator_identity_and_name(hosted, kind):
    captured = legacy.Dimensions(
        party_kind=kind, party_id='private-party', party_name='Private party',
        class_id='class-1', class_name='Visible class',
    )
    assert disclose(hosted, captured).reader_redacted is False
    deny(hosted, kind)
    masked = disclose(hosted, captured)
    assert masked.reader_redacted is True and masked.projection_partial is True
    assert (masked.party_kind, masked.party_id, masked.party_name) == (None,) * 3
    assert (masked.class_id, masked.class_name) == ('class-1', 'Visible class')


@pytest.mark.parametrize('missing_kind', ['customer', 'vendor'])
def test_counterparty_link_collapses_to_none(hosted, missing_kind):
    captured = legacy.CounterpartylinkView(
        link_id='link-1', customer_id='customer-1', vendor_id='vendor-1', active=True,
    )
    assert disclose(hosted, captured).model_dump() == captured.model_dump()
    deny(hosted, missing_kind)
    assert disclose(hosted, captured) is None


class LinkEnvelope(legacy.View):
    """Isolate recursive signal propagation from the parent's field ACL."""
    link: legacy.CounterpartylinkView | None
    links: tuple[legacy.CounterpartylinkView, ...]


@pytest.mark.parametrize('container', ['object', 'tuple'])
def test_collapsed_nested_view_marks_enclosing_capture(hosted, container):
    link = legacy.CounterpartylinkView(
        link_id='link-1', customer_id='customer-1', vendor_id='vendor-1', active=True,
    )
    captured = LinkEnvelope(
        link=link if container == 'object' else None,
        links=(link,) if container == 'tuple' else (),
    )
    assert disclose(hosted, captured).reader_redacted is False
    deny(hosted, 'vendor')
    masked = disclose(hosted, captured)
    assert masked.reader_redacted is True
    assert masked.link is None
    assert 'link-1' not in masked.model_dump_json()


def test_hidden_identity_marks_view_without_hiding_note_body(hosted):
    captured = legacy.NoteView(
        id='note-1', version=2, created_at='2026-09-01T00:00:00Z',
        created_by='A', created_via='cli', updated_at='2026-09-02T00:00:00Z',
        updated_by='A', updated_via='cli', record_type='customer',
        record_id='customer-1', body='Captured note', author_id='B', interface='cli',
        at='2026-09-01T00:00:00Z', edited_at=None, kind='note',
    )
    full = disclose(hosted, captured, who='H')
    assert full.author_id == 'B' and full.reader_redacted is False
    masked = disclose(hosted, captured)
    assert masked.author_id is None
    assert masked.body == captured.body
    assert masked.reader_redacted is True and masked.projection_partial is True
    assert 'version' not in masked.model_dump()


def test_origin_permission_and_nested_signal_use_captured_owner_at_cutoff(hosted, monkeypatch):
    # Only the origin's immutable audit lookup uses this tiny in-memory store;
    # require() and identity visibility remain the real authenticated audience.
    engine = sa.create_engine('sqlite://')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE audit_events (id TEXT, seq INTEGER)')
            conn.exec_driver_sql('CREATE TABLE audit_entries (event_id TEXT, record_id TEXT, record_type TEXT)')
            conn.exec_driver_sql("INSERT INTO audit_events VALUES ('old', 3), ('later', 9)")
            conn.exec_driver_sql("INSERT INTO audit_entries VALUES ('old', 'source-1', 'term'), ('later', 'source-1', 'item')")
            captured = legacy.Origins.model_validate({
                'terms': {'kind': 'default', 'source_id': 'source-1'},
            })
            with audience(hosted) as reader, monkeypatch.context() as patch:
                patch.setattr(reader.reader.session, 'company', SimpleNamespace(conn=conn))
                full = projection._disclose_company(reader, 'C', captured, cutoff=3)
                assert full.model_dump() == captured.model_dump()
                assert full.reader_redacted is False
            deny(hosted, 'term')
            with audience(hosted) as reader, monkeypatch.context() as patch:
                patch.setattr(reader.reader.session, 'company', SimpleNamespace(conn=conn))
                masked = projection._disclose_company(reader, 'C', captured, cutoff=3)
                origin = masked.values[0].origin
                assert origin.source_id is None and origin.kind == 'default'
                assert origin.reader_redacted is True
                assert masked.values[0].reader_redacted is True
                assert masked.reader_redacted is True and masked.projection_partial is True
                # A later ambiguous owner is corrupt, not an excuse to retain a
                # cached earlier permission or reinterpret the source's kind.
                with pytest.raises(BookflowError) as caught:
                    projection._disclose_company(reader, 'C', captured, cutoff=9)
                assert caught.value.code == 'E_VALIDATION'
            assert captured.values[0].origin.source_id == 'source-1'
    finally:
        engine.dispose()


def test_internal_only_omission_is_partial_but_not_reader_redaction(hosted):
    captured = legacy.AllocationCapture(
        source_document_id='source-1', source_revision_id='revision-1',
        source_line_id='line-1', root_document_id='root-1', root_line_id='root-line-1',
        source_basis_hash='private-integrity-hash', quoted_quantity_microunits=1_000_000,
        quoted_base_quantity_microunits=1_000_000, quoted_net_minor_units=100,
        denominator='1000000', spans=(),
    )
    projected = disclose(hosted, captured)
    assert projected.source_basis_hash is None
    assert projected.projection_partial is True
    assert projected.reader_redacted is False
    assert projected.source_document_id == captured.source_document_id
    assert 'source_basis_hash' not in projected.model_dump()
    assert 'reader_redacted' not in projected.model_dump()
