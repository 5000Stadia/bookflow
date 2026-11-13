"""Freeze purchase order proposals using the native non-posting calculation."""
from copy import deepcopy
import json

from bookflow.company import purchase_orders as orders
from bookflow.company.purchase_order_models import PurchaseOrderPostInput, PurchaseOrderUpdateInput
from bookflow.core import unit_price
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.money import Money


def _reference(value):
    return value['id'] if value else None


def _payload(view, *, correction, supplied=None):
    profile, currency = view['profile'], view['currency']
    raw = dict(date=view['date'], vendor=profile['vendor']['id'], number=view['number'],
               expected_date=profile['expected_date'], ship_to=profile['ship_to'],
               terms=_reference(profile['terms']), reference=profile['reference'],
               class_id=_reference(profile['class_id']), memo=view['memo'], lines=[])
    if correction:
        raw['status'] = view['status']
    for index, value in enumerate(view['lines']):
        facts = value['line_snapshot']
        line = dict(description=value['description'], customer=_reference(facts['customer']),
                    billable=facts['billable'], class_id=_reference(facts['class_id']),
                    class_mode='value' if facts['class_id'] else 'none')
        line['item' if facts['item'] else 'account'] = _reference(facts['item'] or facts['account'])
        if value['quantity_microunits'] is not None:
            line.update(quantity=format_quantity_micro_units(value['quantity_microunits']),
                        rate=unit_price.to_dict(unit_price.row_units(value, 'rate'), currency))
        else:
            line['amount'] = Money(value['amount_minor_units'], currency).to_dict()
        if correction and (supplied is None or (index < len(supplied) and supplied[index].get('line_id'))):
            line['line_id'] = value['line_id']
        raw['lines'].append(line)
    return raw


def calculate(s, ctx, payload, operation='create', original_id=None, original_version=None):
    """Normalize exact native commercial facts; drafts reserve no number or receiving capacity."""
    if operation not in ('create', 'correct'):
        raise BookflowError('E_APPROVAL_STATE', message='Purchase orders support create and correct proposals only.',
                            details={'allowed_actions': ['create', 'correct']})
    model = PurchaseOrderPostInput if operation == 'create' else PurchaseOrderUpdateInput
    raw = deepcopy(payload)
    if operation == 'correct':
        if not original_id or original_version is None:
            raise BookflowError('E_VALIDATION', message='Correction requires the original purchase order and exact version.')
        raw.update(purchase_order=original_id, expected_version=original_version)
    inp = model.model_validate(raw)
    verb = 'post' if operation == 'create' else 'update'
    first = orders._prepare_native(s, ctx, inp, verb)
    view = first.preview.revision.model_dump(mode='json')
    normalized = _payload(view, correction=operation == 'correct', supplied=raw.get('lines'))
    # Placement is native commercial evidence even though the public line output has no site column.
    semantic = first.data.get('semantic') or orders._saved_semantic(s, orders.revision(s, orders.resolve(s, original_id)))
    for line, facts in zip(normalized['lines'], semantic['lines'], strict=True):
        if facts.get('site_id'):
            line['site'] = facts['site_id']
    if operation == 'correct':
        normalized.update(purchase_order=orders.resolve(s, original_id)['id'], expected_version=original_version)
    elif inp.number is None:
        normalized.pop('number')
    normalized = model.model_validate(normalized).model_dump(mode='json', exclude_unset=True)
    plan = orders._prepare_native(s, ctx, model.model_validate(normalized), verb)
    commercial = deepcopy(plan.data.get('semantic'))
    if commercial is None:
        commercial = orders._saved_semantic(s, orders.revision(s, orders.resolve(s, original_id)))
    if operation == 'create' and inp.number is None:
        commercial.pop('number', None)
    snapshot = dict(commercial=commercial, sources=[], ledger=[], stock=None,
                    total=plan.preview.total.model_dump(mode='json'))
    plan.data['display_sources'] = dict(purchase_order=None, receipts=[])
    return normalized, json.loads(json.dumps(snapshot)), plan


def prior_payload(s, header):
    """Complete native correction input for the captured prior released revision."""
    revision = orders.revision(s, header)
    view = orders.revision_output(s, revision).model_dump(mode='json')
    payload = _payload(view, correction=True)
    for line, saved in zip(payload['lines'], orders.saved_lines(s, revision), strict=True):
        if saved.get('site_id'):
            line['site'] = saved['site_id']
    payload.update(purchase_order=header['id'], expected_version=header['version'])
    return PurchaseOrderUpdateInput.model_validate(payload).model_dump(mode='json', exclude_unset=True)


def native_plan(s, ctx, inp, operation, inverse_snapshot=None):
    """Prepare the native aggregate and require an inverse to reproduce all prior facts."""
    plan = orders._prepare_native(s, ctx, inp, operation)
    if inverse_snapshot is None:
        return plan
    return validate_inverse(plan, inverse_snapshot)


def validate_inverse(plan, inverse_snapshot):
    """Restore provenance and reject any changed fact in a native inverse plan."""
    if not plan.data.get('changed'):
        raise BookflowError('E_APPROVAL_STATE', message='The correction inverse has no native effect.')
    semantic = plan.data['semantic']
    semantic['profile']['origins'] = deepcopy(inverse_snapshot['profile']['origins'])
    rows = plan.data['pending']['purchase_order_lines']
    prior_lines = {line['line_id']: line for line in inverse_snapshot['lines']}
    for line, row in zip(semantic['lines'], rows, strict=True):
        previous = prior_lines.get(line['line_id'])
        if previous is None:
            raise BookflowError('E_APPROVAL_STALE', message='The inverse cannot restore prior ordered line identities.')
        line['profile']['origins'] = deepcopy(previous['profile']['origins'])
        row['line_snapshot'] = orders.json_text(line['profile'])
    if semantic != inverse_snapshot:
        raise BookflowError('E_APPROVAL_STALE', message='The native inverse cannot restore the exact captured prior facts.',
                            details={'changed_fields': orders._changes(inverse_snapshot, semantic)})
    revision = plan.data['pending']['purchase_order_revisions'][0]
    revision['profile_snapshot'] = orders.json_text(semantic['profile'])
    from bookflow.company.purchase_order_facts import PurchaseOrderProfile, PurchaseOrderLineProfile
    preview_revision = plan.preview.revision.model_copy(update={
        'profile': PurchaseOrderProfile.model_validate(semantic['profile']),
        'lines': [value.model_copy(update={'line_snapshot': PurchaseOrderLineProfile.model_validate(line['profile'])})
                  for value, line in zip(plan.preview.revision.lines, semantic['lines'], strict=True)]})
    plan.preview = plan.preview.model_copy(update={'revision': preview_revision})
    return plan
