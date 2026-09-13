"""Migration inventories follow actual ancestry and the declarations driving rebuilds."""
import importlib

from tests import test_bill_payment_migration as inventory


def test_rebuild_inventory_includes_the_materialization_rebuild(monkeypatch):
    # Isolate this migration's declaration: a future rebuild must not hide its omission.
    monkeypatch.setattr(inventory, '_revisions_after', lambda revision: ['co0044'])
    rebuilt = inventory._rebuilt_since('co0043')
    assert {'reconciliation_keys', 'reconciliation_effect_versions'} <= rebuilt


def test_guard_inventory_follows_a_lower_numbered_successor(monkeypatch):
    purchase = importlib.import_module(
        'bookflow.storage.company_migrations.versions.0035_purchase_orders')
    assert purchase.down_revision == 'co0037'
    assert 'co0035' in inventory._revisions_after('co0037')
    # A replacement declared on this real successor must not be hidden by its name.
    monkeypatch.setattr(purchase, 'REPLACED', ('guard_from_lower_numbered_successor',), raising=False)
    assert 'guard_from_lower_numbered_successor' in inventory._superseded_after('co0037')
