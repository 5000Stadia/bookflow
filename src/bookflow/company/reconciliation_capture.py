"""The only way a captured population comes into existence.

A stored opening or certificate carries a `Population` blob saying what was true at its cutoff:
which versions were the heads, and what the account's signed general-ledger total was. That blob
is what `validate` checks a capture against for the rest of its life, and nothing re-derives it
afterwards -- so it has to be right when it is written, and the way to guarantee that is for the
only function that can produce one to be the one that proves it.

`population` runs the proof and returns the blob. A writer cannot assemble a capture without
calling it, the way a posting write cannot reach the database without passing the trigger that
enqueues it: what makes something a proven capture is that it went through the prover.
"""
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company.reconciliation_preparation import account_population, require
from bookflow.company.reconciliation_proof import prove
from bookflow.company.reconciliation_storage_validation import population_fingerprint


def members(snapshot, account_id, cutoff, *, opening):
    """The rows a capture is made of, in the shape each kind of capture takes.

    An opening names what was outstanding when the account was adopted, so only live movements
    dated on or before it can be members. A certificate names the whole account as it stood, so
    every current head belongs to it -- including the voided and the future-dated, which is how
    a later reader can tell an entry that was never there from one that has since gone away.
    """
    values = [v for v in snapshot.current.values() if v['account_id'] == account_id]
    if opening:
        values = [v for v in values if v['active'] and v['effective_date'] <= cutoff]
    return tuple(sorted(values, key=lambda v: v['key_id']))


def population(snapshot, account_id, cutoff, *, opening):
    """Prove the account against the live ledger and return what may then be stored.

    The proof is `account_population`, which is the same one every read runs: it refuses an
    account whose stored effects do not sum to its general ledger at this cutoff. What is
    returned is derived from the rows that proof just accepted, so the blob and the members can
    never describe two different populations.
    """
    account_population(snapshot, account_id, cutoff)
    history, current = adapters.enumerate_graph(snapshot.source)
    total, gl = prove(snapshot.source, history, current, account_id, cutoff)
    require(total == gl, 'E_RECONCILIATION_SOURCE_INVALID')
    values = members(snapshot, account_id, cutoff, opening=opening)
    return dict(format=1, account_id=account_id,
                currency=snapshot.source.accounts[account_id]['currency'], cutoff=cutoff,
                version_ids=[v['id'] for v in values], signed_gl_total=total,
                source_fingerprint=population_fingerprint(values))
