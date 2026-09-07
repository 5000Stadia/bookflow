"""Private source-plan composition, without persistence or shortened aggregates."""
from dataclasses import dataclass
from bookflow.core.registry import Plan
from bookflow.core.errors import BookflowError
from bookflow.company import payments, sales, deposit_sources


@dataclass(frozen=True)
class PreparedSource:
    action: str
    plan: Plan
    cash: deposit_sources.CashSource | None


def prepare(s, ctx, inp, action, *, expected_fingerprint):
    """Re-resolve the original typed source intent and retain every owning fact.

    The caller must supply the fingerprint of its earlier source preview. This
    is not an apply hook: G2 must still validate the complete aggregate, reconcile
    memberships, and persist every participant in one transaction.
    """
    from bookflow.company.payment_models import PaymentUpdateInput, PaymentVoidInput
    from bookflow.company.sales_models import SalesReceiptUpdateInput, SalesReceiptVoidInput
    models={'payment_update':PaymentUpdateInput,'payment_void':PaymentVoidInput,
            'sales_receipt_update':SalesReceiptUpdateInput,'sales_receipt_void':SalesReceiptVoidInput}
    if action not in models or type(inp) is not models[action]:
        raise BookflowError('E_VALIDATION')
    payment = action.startswith('payment_')
    operation = action.rsplit('_', 1)[1]
    from bookflow.core.ids import is_ulid
    selector=inp.payment if payment else inp.sales_receipt
    if not is_ulid(selector):
        raise BookflowError('E_VALIDATION')
    plan = payments.prepare(s, ctx, inp, operation) if payment else sales.prepare(s, ctx, inp, 'sales_receipt', operation)
    data = plan.data
    if data.get('recovered'):
        raise BookflowError('E_VALIDATION', message='A recovered operation is not a new source effect.')
    fingerprint = data.get('fingerprint', getattr(plan.preview, 'facts_fingerprint', None))
    # Sales void has no source fingerprint in its owning contract: it uses
    # expected_version plus the coordinator's complete guard. Do not fabricate
    # a fingerprint or relax the payment/update comparison.
    absent_fingerprint = action == 'sales_receipt_void' and fingerprint is None and expected_fingerprint is None
    if not absent_fingerprint and (not expected_fingerprint or fingerprint != expected_fingerprint):
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts' if payment else 'sales_facts'})
    if payment:
        if operation == 'void':
            from bookflow.company.payment_cancellation import validate
        else:
            from bookflow.company.payment_corrections import validate
    else:
        from bookflow.company.sales_validation import validate
    validate(plan, s, ctx)
    if operation == 'void':
        cash = None
    elif data.get('changed') is False:
        cash = deposit_sources.load(s, inp.payment if payment else inp.sales_receipt)
    else:
        import sqlalchemy as sa
        from bookflow.company import schema as c
        account = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == 'undeposited_funds')).mappings().one()
        deposit_sources.require(bool(account['active']))
        header = data['header']
        graph = deposit_sources.graph(s, header['id'], data['pending'], header)
        # Moving a source out of UF is an explicit absent result, never a retained
        # cash source. G2 must reject retaining that row in the deposit.
        name = 'payment_profiles' if payment else 'sales_profiles'
        profiles = [r for r in graph[name] if r['revision_id'] == header['current_revision_id']]
        column = 'deposit_account_id' if payment else 'control_account_id'
        if profiles[0][column] != account['id']:
            cash = None
        else:
            cash = deposit_sources.project(graph, uf_account=account['id'], home_currency=s.company_info_row['home_currency'])
    return PreparedSource(action, plan, cash)


def preview_source_effect(s, ctx, action, *, provenance):
    """Prepare a full keyless owned source plan; never execute or claim it."""
    from bookflow.company.deposit_coordinate_models import (
        PaymentUpdateAction, SalesReceiptUpdateAction, PaymentVoidAction,
        SalesReceiptVoidAction, PreparedSource as Source, source_identity)
    from bookflow.company.payment_models import PaymentVoidIntent, EffectProvenance
    from bookflow.company.sales_models import SalesReceiptVoidInput
    from bookflow.company import payment_corrections, payment_cancellation
    if type(action) not in (PaymentUpdateAction, SalesReceiptUpdateAction, PaymentVoidAction, SalesReceiptVoidAction) or type(provenance) is not EffectProvenance:
        raise BookflowError('E_VALIDATION')
    try:
        identity = source_identity(action)
    except ValueError:
        raise BookflowError('E_VALIDATION') from None
    if isinstance(action, PaymentUpdateAction):
        plan = payment_corrections.prepare_effect(s, ctx, action.input, provenance)
        payment_corrections.validate(plan, s, ctx)
    elif isinstance(action, PaymentVoidAction):
        inp = PaymentVoidIntent(payment=identity, expected_version=action.expected_version)
        owner = payment_cancellation.prepare_all_active_void if action.unapply == 'all_active' else payment_cancellation.prepare_void_effect
        plan = owner(s, ctx, inp, provenance)
        payment_cancellation.validate(plan, s, ctx)
    else:
        inp = action.input if isinstance(action, SalesReceiptUpdateAction) else SalesReceiptVoidInput(sales_receipt=identity, expected_version=action.expected_version)
        plan = sales.prepare(s, ctx, inp, 'sales_receipt', action.kind.rsplit('_', 1)[1], provenance=provenance)
        from bookflow.company.sales_validation import validate
        validate(plan, s, ctx)
    fingerprint = plan.data.get('fingerprint', getattr(plan.preview, 'facts_fingerprint', None))
    data = plan.data
    if action.kind.endswith('_void'):
        cash = None
    elif not plan.preview.changed:
        cash = deposit_sources.load(s, identity)
    else:
        import sqlalchemy as sa
        from bookflow.company import schema as c
        account = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == 'undeposited_funds')).mappings().one()
        graph = deposit_sources.graph(s, identity, data['pending'], data['header'])
        payment = action.kind.startswith('payment_')
        profiles = graph['payment_profiles' if payment else 'sales_profiles']
        profile = next(r for r in profiles if r['revision_id'] == data['header']['current_revision_id'])
        if profile['deposit_account_id' if payment else 'control_account_id'] != account['id']:
            cash = None
        else:
            deposit_sources.require(bool(account['active']))
            cash = deposit_sources.project(graph, uf_account=account['id'], home_currency=s.company_info_row['home_currency'])
    from bookflow.company import reconciliation_adapters
    bank_changes = reconciliation_adapters.prospective(s, ctx, plan)
    return Source(action, provenance, plan, cash, fingerprint, bank_changes)


def prepare_source_effect(s, ctx, action, *, provenance, expected_fingerprint):
    """Reprepare a source against its earlier exact owned preview fingerprint."""
    from bookflow.company.deposit_coordinate_models import SalesReceiptVoidAction
    prepared = preview_source_effect(s, ctx, action, provenance=provenance)
    absent = isinstance(action, SalesReceiptVoidAction) and prepared.source_fingerprint is None and expected_fingerprint is None
    if not absent and (not expected_fingerprint or prepared.source_fingerprint != expected_fingerprint):
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts' if action.kind.startswith('payment_') else 'sales_facts'})
    return prepared
