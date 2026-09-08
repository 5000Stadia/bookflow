"""Fixed declaration caching cannot bypass validation of fresh policy values."""
from dataclasses import replace
import pytest
from bookflow.hub import permission_catalog as c


def test_fixed_metadata_is_resolved_once_but_each_value_is_checked(monkeypatch):
    c._declared_fields.cache_clear()
    original = c.get_type_hints
    calls = []
    def observe(cls):
        calls.append(cls)
        return original(cls)
    monkeypatch.setattr(c,'get_type_hints',observe)
    valid = c.Requirement('ledger.read','member')
    c._check(valid,c.Requirement)
    for bad in (replace(valid,capability=3),replace(valid,threshold=True),replace(valid,capability='*')):
        with pytest.raises(c.PolicyInputError):c._check(bad,c.Requirement)
    c._check(valid,c.Requirement)
    assert calls == [c.Requirement]
    c._declared_fields.cache_clear()


def test_cached_and_uncached_catalog_checks_have_identical_results(monkeypatch):
    catalogs = (c.FROZEN_CATALOG,replace(c.FROZEN_CATALOG,version=7),
                replace(c.FROZEN_CATALOG,capabilities=list(c.FROZEN_CATALOG.capabilities)))
    def outcomes():
        results=[]
        for value in catalogs:
            try:results.append(('ok',c._normal_catalog(value)))
            except c.PolicyInputError as error:results.append(('error',error.args))
        return results
    cached=outcomes()
    monkeypatch.setattr(c,'_field_types',lambda cls:tuple(c.get_type_hints(cls).items()))
    assert outcomes() == cached
    assert cached[0][0]=='ok' and all(x[0]=='error' for x in cached[1:])
