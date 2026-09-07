"""Reconciliation activation metadata; owned successor must supply its resolver."""
from bookflow.storage.migrate import FeatureRevision, feature_admission

RECONCILIATION = FeatureRevision('company', None)


def reconciliation_status(db):
    resolver = feature_admission(db, RECONCILIATION, resolver=None)
    assert resolver is None
    return 'absent'
