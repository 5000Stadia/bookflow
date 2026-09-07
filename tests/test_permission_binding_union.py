"""Union provenance and explicit catalog admission; no production adoption."""
from dataclasses import replace
import pytest
from bookflow.hub import permission_runtime as r, permission_snapshot as s
from bookflow.storage.engine import open_database
from tests.test_permission_runtime import path
from tests.permission_admin_support import snapshot
from tests.test_permission_snapshots import BUNDLE, install_fixture_policy

OLD='18dc986456d07c27f0137fd99192611beaa7f2e5'
BASE='3990636d2a2d3c683a0e806e6635a4a64c0075b0'


def test_bundle_source_base_identity_and_owned_current_snapshot(path):
    current=r.catalog_bundle();old=replace(current,source_commit=OLD)
    assert current.source_commit==BASE
    assert current.descriptor==old.descriptor and current.source_inventory_digest==old.source_inventory_digest
    assert s._bundle(current)!=s._bundle(old)
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        prior=s.load_root(db,catalog=old)
        fresh=s.load_root(db,catalog=current)
        assert prior.stamp.authority_rows_digest==fresh.stamp.authority_rows_digest
        assert prior.stamp.bundle_digest!=fresh.stamp.bundle_digest
        with pytest.raises(s.SnapshotError):
            s.observe_pair(prior,prior,old_catalog=current,new_catalog=current,visibility=r.VISIBILITY)
        observed=r.observe_current(db)
        assert observed.snapshot.old.stamp.bundle_digest==s._bundle(current)
        assert snapshot(db.raw)==before


def test_historical_descriptor_is_not_auto_adopted(path):
    with open_database(path,writable=True) as db:
        install_fixture_policy(db.raw,BUNDLE)
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        with pytest.raises(s.SnapshotError):s.load_root(db,catalog=r.catalog_bundle())
        assert snapshot(db.raw)==before


def test_private_source_metadata_does_not_register_or_enable_delete():
    from bookflow.hub import permission_catalog as c
    catalog=r.catalog_bundle().descriptor
    for name in ('transaction.journal_entry.delete','transaction.invoice.delete',
                 'transaction.sales_receipt.delete','transaction.payment.delete'):
        spec=next(x for x in catalog.capabilities if x.name==name)
        assert spec.company_thresholds==('standard',) and spec.registered_thresholds==()
        assert not any(x.requirement.capability==name for x in catalog.defaults)
        actions=[x for x in catalog.company_actions if any(y.capability==name for y in x.requirements)]
        assert actions and all(not x.available for x in actions)
    assert catalog.capabilities==c.FROZEN_CATALOG.capabilities
    assert catalog.company_actions==c.FROZEN_CATALOG.company_actions
    assert catalog.defaults==c.FROZEN_CATALOG.defaults
    for requirement in (c.Requirement('unknown','member'),c.Requirement('transaction.invoice.delete','member')):
        bad=replace(catalog,conditional_sources=(c.ResourceSource('owned.invalid',(('owned.py',1),),(requirement,)),))
        with pytest.raises(c.PolicyInputError):c.catalog_manifest(bad)
