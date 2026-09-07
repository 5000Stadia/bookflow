"""Expected legacy recovery outcomes, independently enumerated from call paths."""
from bookflow.core import registry


def test_only_affected_legacy_contracts_advertise_pending_recovery():
    affected={'payment receive','payment apply','payment selection create','payment selection update','payment selection clear','payment selection show','payment selection query'}
    unaffected={'payment unapply','payment void','payment update','payment show','payment query','payment selection items'}
    for name in affected:assert 'E_RECOVERY_PENDING' in registry.get(name).error_codes,name
    for name in unaffected:assert 'E_RECOVERY_PENDING' not in registry.get(name).error_codes,name
    assert 'E_QUERY_STALE' in registry.get('payment selection query').error_codes
