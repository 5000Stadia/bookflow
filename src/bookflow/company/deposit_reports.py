"""Complete, pure current/effective deposit arithmetic.

No database discovery, authorization, cursor or public entry point. These are
internal transformations of a complete read-owner validated population.
"""
from bookflow.company.deposit_report_models import (
    AccountRoleTotal, BankRoleMovement, Composition, CompleteRelation, DepositDetailFilter, MovementTotals,
    Population, ReportDeposit, ReportRow, ReportTotals, ScopedAmount,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64


def _invalid():
    raise BookflowError('E_DEPOSIT_SOURCE_INVALID')


def _composition(effect, sign=1):
    values = dict(
        source=sum(r.source.cash_minor_units for r in effect.intent.sources),
        positive_additional=sum(max(0, r.units) for r in effect.intent.additional),
        negative_additional=sum(min(0, r.units) for r in effect.intent.additional),
        posting_total=effect.posting_total, subtotal=effect.subtotal,
        bank_total=effect.bank_total, cash_back=effect.cash_back,
    )
    return Composition(**{k: _require_i64(sign*v, field=k) for k, v in values.items()})


def _sum(rows):
    # Do not range-check prefixes: signed cancellation is exact before output.
    return Composition(**{
        key: _require_i64(sum(getattr(r.composition, key) for r in rows), field=key)
        for key in Composition.model_fields
    })


def aggregate(deposits: tuple[ReportDeposit, ...], inp: DepositDetailFilter,
              *, currency: str, destination_id: str | None) -> CompleteRelation:
    """Aggregate all selected rows before any future transport page is sliced.

    destination_id is the read owner's resolved selector, not a new lookup.
    This function does not certify the input population's completeness/rights.
    """
    statuses = inp.statuses()
    if (inp.deposit_to is None) != (destination_id is None):
        _invalid()
    if len({d.id for d in deposits}) != len(deposits):
        _invalid()
    population = Population(projection=inp.projection, destination_id=destination_id,
                            current_status_filter=inp.status)
    all_rows = []
    for document in deposits:
        revisions = {r.revision_id: r for r in document.revisions}
        batches = {b.id: b for b in document.batches}
        if len(revisions) != len(document.revisions) or len(batches) != len(document.batches):
            _invalid()
        if document.current_revision_id not in revisions:
            _invalid()
        # Structural conversion checks are not the owner's independent validator.
        for revision in revisions.values():
            if revision.effect.inverse_of is not None or revision.effect.intent.deposit_id != document.id or revision.effect.intent.currency != currency:
                _invalid()
        for batch in batches.values():
            if batch.revision_id not in revisions:
                _invalid()
            if batch.reverses_batch_id:
                target = batches.get(batch.reverses_batch_id)
                if target is None or target.kind == 'reversal' or target.revision_id != batch.revision_id:
                    _invalid()
            if batch.replaces_batch_id and batch.replaces_batch_id not in batches:
                _invalid()
        if document.status not in statuses:
            continue
        selected = (None,) if inp.projection == 'current' else document.batches
        for batch in selected:
            revision = revisions[document.current_revision_id if batch is None else batch.revision_id]
            effect = revision.effect
            bank = effect.intent.bank
            if destination_id is not None and bank.id != destination_id:
                continue
            sign = -1 if batch is not None and batch.kind == 'reversal' else 1
            roles = []
            seen = set()
            for role in revision.bank_effects:
                if role.transaction_id != document.id or role.currency != currency or role.identity in seen:
                    _invalid()
                seen.add(role.identity)
                if role.active and role.signed_debit:
                    roles.append(BankRoleMovement(role=role.role, row_id=role.row_id,
                        account_id=role.account_id,
                        signed_debit=_require_i64(sign*role.signed_debit, field='signed_debit'),
                        statement_amount=_require_i64(sign*role.statement_amount, field='statement_amount'),
                        kind='captured_business_role' if batch is None else 'accounting_movement'))
            all_rows.append(ReportRow(
                currency=currency, transaction_id=document.id, revision_id=revision.revision_id,
                current_revision_id=document.current_revision_id, current_status=document.status,
                number=revision.number, date=effect.intent.date if batch is None else batch.effective_date,
                destination_id=bank.id, destination_name=bank.name, batch=batch,
                composition=_composition(effect, sign), bank_roles=tuple(roles), source_count=len(effect.intent.sources),
                additional_count=len(effect.intent.additional), cell_count=len(effect.cells),
                effective_current_bank_total=(effect.bank_total if document.status == 'posted' else 0)
                    if batch is None else None,
            ))
    all_rows.sort(key=lambda r: (r.date, r.transaction_id.encode('utf-8'),
                                b'' if r.batch is None else r.batch.id.encode('utf-8')))
    rows = tuple(r for r in all_rows if inp.date_from <= r.date <= inp.date_to)
    movement = None
    if inp.projection == 'effective':
        def scoped(value):
            return ScopedAmount(currency=currency, population=population,
                                minor_units=_require_i64(value, field='movement'))
        opening = sum(r.composition.bank_total for r in all_rows if r.date < inp.date_from)
        period = sum(r.composition.bank_total for r in rows)
        closing = sum(r.composition.bank_total for r in all_rows if r.date <= inp.date_to)
        movement = MovementTotals(opening=scoped(opening), period=scoped(period), closing=scoped(closing))
    role_sums = {}
    for row in rows:
        for role in row.bank_roles:
            values = role_sums.setdefault((role.account_id, role.role), [0, 0])
            values[0] += role.signed_debit
            values[1] += role.statement_amount
    def role_amount(value):
        return ScopedAmount(currency=currency, population=population,
                            minor_units=_require_i64(value, field='bank_role_total'))
    roles = tuple(AccountRoleTotal(account_id=account, role=role,
                  signed_debit=role_amount(values[0]), statement_amount=role_amount(values[1]))
                  for (account, role), values in sorted(role_sums.items()))
    return CompleteRelation(rows=rows, totals=ReportTotals(
        currency=currency, population=population, row_count=len(rows),
        deposit_count=len({r.transaction_id for r in rows}),
        source_count=sum(r.source_count for r in rows),
        additional_count=sum(r.additional_count for r in rows), cell_count=sum(r.cell_count for r in rows),
        composition=_sum(rows), movement=movement, account_roles=roles,
    ))
