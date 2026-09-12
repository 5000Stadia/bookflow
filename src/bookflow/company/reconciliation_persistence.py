"""Turn a prepared reconciliation result into the rows that record it.

Everything here is pure: it builds rows and returns them. The command inserts them and then
reloads the account, which re-runs `validate` over the whole aggregate and proves every capture
the operation wrote. That reload is the write-time proof, and it is not something a writer can
forget to ask for -- `prove_written` takes the captures from the operation's own target rows,
which `validate` independently requires to be the complete set.
"""
import json

from bookflow.company import reconciliation_capture as capture
from bookflow.company import reconciliation_commands_models as m
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company.reconciliation_storage_validation import canonical, digest
from bookflow.core.ids import new_id

TARGET_FIELDS = dict(transactions='transaction_id', accounts='account_id', drafts='draft_id',
                     openings='opening_id', certificates='certificate_id')


ISSUER_FIELDS = ('id', 'legal_name', 'home_currency')
ISSUER_PREFIXES = ('address_', 'legal_address_', 'ship_address_')


def issuer(company_info):
    """Who issued this certificate, captured the way a document captures its issuer.

    A certificate is read years later, so the company's name and address at the time belong to
    it rather than being looked up again. `deposit_lifecycle` spells the same shape inline; that
    is one shape in two places and the deposit copy should come here rather than the reverse.
    """
    return {k: v for k, v in company_info.items()
            if k in ISSUER_FIELDS or k.startswith(ISSUER_PREFIXES)}


def created(ctx, actor_id, audit_event_id, at):
    return dict(created_at=at, created_by=actor_id, created_via=ctx.interface.value,
                audit_event_id=audit_event_id)


def receipt(operation_id, *, command, operation_key, request, effect, targets, made):
    """The operation and its item tail, in the shape `validate` proves a receipt in.

    The manifests are counts and hashes over the items actually stored, so a receipt whose tail
    was truncated on the way in cannot describe itself as whole.
    """
    ordered = [dict(kind=t['kind'], id=t['id']) for t in targets]
    items = dict(request=[request], effects=[effect], targets=ordered, generated=[])
    collections = {k: dict(count=len(v), hash=digest(list(v))) for k, v in items.items()}
    intent = dict(schema_version=1, command=command, input=request)
    rows = {'operations': [dict(
        id=operation_id, operation_key=operation_key, command=command, request_schema_version=1,
        original_request_snapshot=canonical(dict(format=1, document=request, canonical_intent=intent,
                                                 collections=collections)),
        canonical_intent_hash=digest(intent), effect_schema_version=1,
        original_effect_snapshot=canonical(dict(format=1, document=effect, collections=collections)),
        **made)]}
    rows['operation_items'] = [dict(operation_id=operation_id, kind=kind, ordinal=i,
                                    facts_snapshot=canonical(value))
                               for kind, values in items.items() for i, value in enumerate(values)]
    for kind in TARGET_FIELDS:
        rows['operation_' + kind] = [dict(operation_id=operation_id, **{TARGET_FIELDS[kind]: t['id']})
                                     for t in targets if t['kind'] == kind]
    return rows


def event(event_id, *, operation_id, audit_event_id, kind, ctx, actor_id, at, principal_id=None):
    return dict(id=event_id, operation_id=operation_id, audit_event_id=audit_event_id,
                actor_id=actor_id, principal_id=principal_id, interface=ctx.interface.value,
                recorded_at=at, reason=ctx.reason, kind=kind, schema_version=1)


def draft(value, *, made, previous_revision_id=None):
    """A draft header and the revision that is currently its content."""
    header = value.header.model_dump(mode='json')
    revision_number = value.version
    return {
        'drafts': [dict(id=value.id, account_id=value.account_id, kind=value.kind,
                        version=value.version, current_revision_id=value.current_revision_id,
                        state=value.state, terminal_operation_id=value.terminal_operation_id, **made)],
        'draft_revisions': [dict(id=value.current_revision_id, draft_id=value.id,
                                 account_id=value.account_id, revision_number=revision_number,
                                 previous_revision_id=previous_revision_id,
                                 header_snapshot=canonical(header),
                                 base_chain_version=value.base_chain_version,
                                 base_opening_id=value.base_opening_id,
                                 base_head_id=value.base_head_id, repair_of_opening_id=None,
                                 repair_of_certificate_id=None, **made)],
        'draft_members': [dict(revision_id=value.current_revision_id, draft_id=value.id,
                               account_id=value.account_id, key_id=v.key_id,
                               version_id=v.version_id, action=v.action, ordinal=i)
                          for i, v in enumerate(value.selections)],
    }


def opening(identity, snapshot, value, *, made, generation=1, predecessor=None):
    """An adopted opening balance and the movements it accounts for."""
    pop = capture.population(snapshot, value.account_id, value.header.opening_date, opening=True)
    chosen = {v.key_id: v.action for v in value.selections}
    members = capture.members(snapshot, value.account_id, value.header.opening_date, opening=True)
    rows = {'openings': [dict(
        id=identity, account_id=value.account_id, generation=generation,
        opening_date=value.header.opening_date, balance=value.header.entered_balance,
        currency=pop['currency'], predecessor_opening_id=predecessor,
        origin_draft_revision_id=value.current_revision_id,
        evidence_snapshot=canonical(value.header.evidence.model_dump(mode='json')),
        authorized_source_snapshot=canonical(pop), **made)]}
    rows['opening_members'] = [dict(opening_id=identity, account_id=value.account_id,
                                    key_id=v['key_id'], version_id=v['id'],
                                    classification=chosen[v['key_id']], ordinal=i)
                               for i, v in enumerate(members)]
    rows['opening_evidence'] = [dict(
        opening_id=identity, ordinal=i, kind=ref.kind, transaction_id=ref.transaction_id,
        attachment_id=getattr(ref, 'attachment_id', None),
        attachment_link_id=getattr(ref, 'attachment_link_id', None),
        captured_evidence=canonical(ref.model_dump(mode='json')))
        for i, ref in enumerate(value.evidence_references)]
    return rows, pop


def certificate(identity, snapshot, value, totals, *, opening_id, covered, prior, issuer, made,
                generation=1, previous=None):
    """A certified statement and the whole account as it stood when it was certified.

    Every current head is a member, not only what was ticked: an outstanding movement is part of
    what the statement says, and a reader a year later has to be able to tell a movement that was
    never there from one that has since been voided.
    """
    account_id = value.account_id
    cutoff = value.header.statement_date
    pop = capture.population(snapshot, account_id, cutoff, opening=False)
    members = capture.members(snapshot, account_id, cutoff, opening=False)
    selected = {v.key_id for v in value.selections}
    rows = {'certificates': [dict(
        id=identity, account_id=account_id, generation=generation, statement_date=cutoff,
        opening_id=opening_id, previous_certificate_id=previous, supersedes_certificate_id=None,
        origin_draft_revision_id=value.current_revision_id,
        beginning_balance=totals.beginning_balance, ending_balance=totals.ending_balance,
        selected_sum=totals.selected_sum, original_difference=totals.difference,
        final_difference=0, positive_sum=totals.positive_sum, negative_sum=totals.negative_sum,
        positive_count=totals.positive_count, negative_count=totals.negative_count,
        currency=pop['currency'],
        convention='card_debt' if members and members[0]['account_type'] == 'credit_card' else 'bank',
        captured_source_snapshot=canonical(pop), issuer_snapshot=issuer, **made)]}

    def classification(row):
        if row['key_id'] in covered:
            return 'opening_covered'
        if row['key_id'] in prior:
            return 'prior_cleared'
        return 'selected' if row['key_id'] in selected else 'outstanding'

    rows['certificate_members'] = [dict(
        certificate_id=identity, account_id=account_id, key_id=v['key_id'], version_id=v['id'],
        classification=classification(v),
        eligible_at_cutoff=int(bool(v['active']) and v['effective_date'] <= cutoff), ordinal=i)
        for i, v in enumerate(members)]
    return rows, pop


def claims(rows, *, account_id, event_id, opening_id, certificate_id):
    """One claim per movement a capture takes responsibility for, and the current index of them.

    A key claimed by the opening is excluded from selection, so no key is ever claimed twice --
    which is the thing `overlapping_claims` refuses and the reason a released claim has to be
    recorded rather than deleted.
    """
    made = []
    for member, owner, field, classification in (
            (rows.get('opening_members', []), opening_id, 'opening_id', 'covered'),
            (rows.get('certificate_members', []), certificate_id, 'certificate_id', 'selected')):
        for row in member:
            if row['classification'] != classification:
                continue
            made.append(dict(id=new_id(), account_id=account_id, key_id=row['key_id'],
                             version_id=row['version_id'], opening_id=None, certificate_id=None,
                             event_id=event_id) | {field: owner})
    return {'claims': made,
            'current_members': [dict(key_id=v['key_id'], claim_id=v['id']) for v in made]}


def chain(*, account_id, currency, convention, opening_id, certificate_id, event_id, statement_date,
          before_version=0, before_opening=None, before_head=None):
    """Where the account's chain now stands, and the one event that moved it there."""
    return {
        'accounts': [dict(account_id=account_id, currency=currency, convention=convention,
                          version=before_version + 1, opening_id=opening_id,
                          head_certificate_id=certificate_id, last_event_id=event_id)],
        'active_certificates': [dict(account_id=account_id, statement_date=statement_date,
                                     certificate_id=certificate_id)],
        'event_accounts': [dict(event_id=event_id, account_id=account_id,
                                before_chain_version=before_version,
                                after_chain_version=before_version + 1,
                                before_opening_id=before_opening, after_opening_id=opening_id,
                                before_head_id=before_head, after_head_id=certificate_id)],
    }


def merge(*parts):
    out = {}
    for part in parts:
        for name, values in part.items():
            out.setdefault(name, []).extend(values)
    return out


# Insertion order: a row goes in after the rows it points at. Written out rather than sorted at
# runtime because the order is a fact about the schema, and a wrong one fails as a foreign key
# violation in the middle of a write rather than as anything a reader could act on.
# `draft_revisions` precedes `drafts`, and `event_accounts` precedes `accounts`, because each of
# those tables carries an insert trigger that looks the other one up: the chain state has to be
# able to point at the event that moved it. The foreign keys back the other way are deferred for
# exactly that reason.
ORDER = ('operations', 'operation_items', 'events', 'draft_revisions', 'drafts', 'draft_members',
         'openings', 'opening_members', 'opening_evidence', 'certificates', 'certificate_members',
         'event_accounts', 'accounts', 'active_certificates', 'claims', 'current_members',
         'operation_accounts', 'operation_transactions', 'operation_drafts', 'operation_openings',
         'operation_certificates', 'event_effects')
