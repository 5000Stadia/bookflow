"""Evidence for a blocked row: the runtime snapshot loader cannot be built as the code stands.

`preparation.snapshot` is the only door to every read and every command in this family, and it
runs `reconciliation_storage_validation.validate`, which re-proves every stored opening and
certificate against a graph the caller supplies (`capture_proof_inventory` demands one per
capture; `capture` re-runs `enumerate_graph` and `prove` on it). The hand-built aggregates in
`test_reconciliation_storage_validation.py` satisfy that by holding the graph they captured in
memory for the length of one test. A loader has no such graph -- it can only read the company as
it is now, and `adapters.graph` has no as-of form -- so the first ordinary correction to a
certified document locks the account out of its own reconciliation entirely.

This file is not a property worth keeping. Whichever way the contradiction is resolved -- proving
captures only where they are written, or giving the adapters a dated graph -- it stops being true
and goes with the change that fixes it.
"""
import sqlalchemy as sa
import pytest

from bookflow.company import schema as sch
from bookflow.company.reconciliation_storage_validation import InvalidStorage, validate
from tests.test_deposit_lifecycle import driver  # noqa: F401
from tests.test_reconciliation_storage_validation import (  # noqa: F401
    account, journal, pair, run, adapters, references, aggregate, blank, COMPANY,
)


def test_a_capture_cannot_be_reproved_against_the_source_once_a_captured_document_changes(client,driver):
    """Evidence for a blocked row, not a property worth keeping.

    `validate` re-proves every stored opening and certificate against a caller-supplied graph
    (`capture_proof_inventory` demands one per capture, and `capture` re-runs `enumerate_graph`
    and `prove` on it). The hand-built aggregates elsewhere in this file satisfy that by holding
    the graph they captured, in memory, for the length of one test. A runtime loader has no such
    graph: it can only read the company as it is now, and `adapters.graph` has no as-of form.

    So the moment an ordinary correction lands on a document a certificate captured, no Snapshot
    can be constructed for that account at all -- while the stored rows themselves stay perfectly
    consistent, which is what the second half asserts. That state is not an edge case: it is the
    only state in which `reconciliation_reports.project` reports a nonzero
    `local_replacement_impact`, so that number is unreachable in production as the code stands.

    Whichever way that is resolved -- re-proving captures only at write time, or giving the
    adapters a dated graph -- this test stops being true and goes with it.
    """
    bank=account(client,'Capture bank');equity=account(client,'Capture equity','equity')
    doc=journal(client,pair(bank,equity,'10'))
    def owned(session):
        out=blank()
        for name in out:
            out[name]=[dict(v) for v in session.company.conn.execute(sa.select(sch.metadata.tables['reconciliation_'+name])).mappings()]
        keys={v['id'] for v in out['keys'] if v['transaction_id']==doc['id']}
        out['keys']=[v for v in out['keys'] if v['id'] in keys]
        out['effect_versions']=[v for v in out['effect_versions'] if v['key_id'] in keys]
        versions={v['id'] for v in out['effect_versions']}
        out['effect_heads']=[v for v in out['effect_heads'] if v['key_id'] in keys]
        for name in ('commercial_versions','deposit_versions'):
            out[name]=[v for v in out[name] if v['id'] in versions]
        for name in ('effect_legs','effect_sources'):
            out[name]=[v for v in out[name] if v['version_id'] in versions]
        return out
    with driver.session() as s:
        rows=owned(s);g=adapters.graph(s,[doc['id']]);refs=references(s)
    stored=aggregate(rows,g,bank)
    validate(stored,source=g,captured_graphs={v['id']:g for n in ('openings','certificates') for v in stored[n]},referenced_rows=refs)

    # An ordinary permitted correction to the very document the certificate captured.
    run(client,'journal update',dict(journal=doc['id'],expected_version=1,memo='Capture correction'))
    with driver.session() as s:
        after=owned(s);live=adapters.graph(s,[doc['id']]);refs=references(s)
    for name,values in stored.items():
        if not after[name]:after[name]=values
    assert len(after['effect_versions'])==2 and len(stored['effect_versions'])==1
    assert stored['certificate_members'][0]['version_id'] not in {v['version_id'] for v in after['effect_heads']}
    captures={v['id']:live for n in ('openings','certificates') for v in after[n]}
    with pytest.raises(InvalidStorage,match='capture_population_completeness'):
        validate(after,source=live,captured_graphs=captures,referenced_rows=refs)
    # The stored rows are not what is wrong. The certificate still names a version that is still
    # stored, still owned by its key, and still carries the amount it was certified at; what is
    # gone is only the graph that once made it the head.
    # The door every read and every command goes through says the same thing, in the words a
    # person would actually be shown: the source is invalid. Nothing about the source is invalid.
    from bookflow.company import reconciliation_preparation as preparation
    with pytest.raises(preparation.ReconciliationError) as raised:
        preparation.snapshot(after,source=live,captured_graphs=captures,referenced_rows=refs,
                             authority_transactions=[v['id'] for v in refs['transactions']])
    assert raised.value.rule=='E_RECONCILIATION_SOURCE_INVALID'

    versions={v['id']:v for v in after['effect_versions']}
    captured_version=versions[stored['certificate_members'][0]['version_id']]
    assert captured_version['signed_debit']==stored['certificates'][0]['selected_sum']
    assert captured_version['key_id']==stored['certificate_members'][0]['key_id']
