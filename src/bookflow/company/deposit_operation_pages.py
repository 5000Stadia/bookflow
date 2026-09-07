"""Private complete immutable operation collections and binding-owned pages."""
import json
from typing import Literal
from pydantic import TypeAdapter, JsonValue
from bookflow.company import schema as c, document_effects as rows, payment_queries as q
from bookflow.company import deposit_operations as operations, deposit_dependency_history as history
from bookflow.company.deposit_models import Frozen
from bookflow.company.deposit_dependency_models import PageInput
from bookflow.company.deposit_coordinate_models import CoordinateTransactionsRow
from bookflow.core.errors import BookflowError

Kind = Literal['request_sources','request_additional','memberships','document_changes','cash_allocations','bank_changes',
               'source_components','source_applications','source_allocations','source_document_changes']
KINDS=TypeAdapter(Kind)
DOMAIN=b'deposit-operation-page-v2\0'
PREVIEW_DOMAIN=b'deposit-coordinate-preview-page-v1\0'


class OperationPage(Frozen):
    kind: Kind
    items: tuple[dict[str, JsonValue], ...]
    total_count: int
    digest: str
    next_cursor: str | None


def collections(output):
    coordinate=output.command=='deposit coordinate'
    effect=output.effect.deposit if coordinate else output.effect
    result=dict(request_sources=[v.model_dump(mode='json') for v in effect.financial.intent.sources],
        request_additional=[v.model_dump(mode='json') for v in effect.financial.intent.additional],
        memberships=[v.model_dump(mode='json') for v in effect.memberships],
        document_changes=[v.model_dump(mode='json') for v in effect.headers],
        cash_allocations=[v.model_dump(mode='json') for v in effect.financial.cells],
        bank_changes=[v.model_dump(mode='json') for v in effect.bank_effects])
    if not coordinate:return result
    source=output.effect.source
    if source.payment_effect is not None:
        owned=source.payment_effect.effect
        result.update(source_components=[v.model_dump(mode='json') for v in owned.source_components],
            source_applications=[v.model_dump(mode='json') for v in owned.applications],
            source_allocations=[v.model_dump(mode='json') for v in owned.allocations],
            source_document_changes=[v.model_dump(mode='json') for v in owned.document_changes])
    else:
        from bookflow.company.deposit_coordinate_models import SalesComponentItem, SalesHeaderItem, SalesLineItem, SalesWorkItem
        inserted=source.inserted
        lines={v.id:v for v in inserted.document_lines}
        components=[]
        for line in inserted.sales_line_profiles:
            components.append(SalesComponentItem(kind='sale_net',line_id=lines[line.document_line_id].line_id,
                tax_item_id=None,revision_id=line.revision_id,document_line_id=line.document_line_id,
                physical_component_id=None,capacity=line.net_minor_units).model_dump(mode='json'))
        for tax in inserted.sales_tax_components:
            components.append(SalesComponentItem(kind='sale_tax',line_id=lines[tax.document_line_id].line_id,
                tax_item_id=tax.tax_item_id,revision_id=tax.revision_id,document_line_id=tax.document_line_id,
                physical_component_id=tax.id,capacity=tax.tax_minor_units).model_dump(mode='json'))
        changes=[]
        if source.before_header!=source.after_header:
            changes.append(SalesHeaderItem(kind='header',before=source.before_header,after=source.after_header,
                revisions=inserted.transaction_revisions,profiles=inserted.sales_profiles,
                tax_attributions=inserted.sales_tax_attributions).model_dump(mode='json'))
        for line in sorted(inserted.document_lines,key=lambda v:v.line_id):
            changes.append(SalesLineItem(kind='line',line=line,profile=next(v for v in inserted.sales_line_profiles if v.document_line_id==line.id),
                tax_components=tuple(v for v in inserted.sales_tax_components if v.document_line_id==line.id),
                tax_keys=tuple(v for v in inserted.sales_tax_attribution_lines if v.document_line_id==line.id)).model_dump(mode='json'))
        for work in sorted(inserted.work_billing_allocations,key=lambda v:v.id):
            changes.append(SalesWorkItem(kind='work',allocation=work).model_dump(mode='json'))
        result.update(source_components=components,source_applications=[],source_allocations=[],source_document_changes=changes)
    return result


def authorized_output(s,saved,binding,*,write=False):
    history.execution_binding(s,binding)
    indexed=rows.rows(s,c.deposit_operation_targets,c.deposit_operation_targets.c.operation_id==saved['id'])
    targets=tuple(sorted(v['transaction_id'] for v in indexed))
    request=json.loads(saved['request_snapshot'])
    if not targets or saved['transaction_id'] not in targets or list(targets)!=sorted(request['resolved_transaction_ids']):
        raise BookflowError('E_INTERNAL',message='Incomplete operation evidence.')
    try:
        history._authorize_binding_graph(s,binding,targets,write=write)
    except BookflowError as error:
        if error.code=='E_PERMISSION':raise BookflowError('E_PERMISSION',details={}) from None
        raise
    output=operations.decode_output(saved['effect_snapshot'],saved['command'])
    if output.operation_id!=saved['id'] or output.current.id!=saved['transaction_id']:
        raise BookflowError('E_INTERNAL')
    updates=dict(current=operations.state(s,saved['transaction_id']))
    if output.command=='deposit coordinate':
        if output.effect.target_ids!=targets:raise BookflowError('E_INTERNAL')
        current=[]
        for identity in targets:
            found=rows.rows(s,c.transactions,c.transactions.c.id==identity)
            if len(found)!=1:raise BookflowError('E_INTERNAL')
            current.append(CoordinateTransactionsRow.model_validate_json(q.canonical(found[0])))
        updates['current_headers']=tuple(current)
    return output.model_copy(update=updates)


def _page(s,values,kind,page,binding,recipe,domain):
    recipe=dict(recipe,kind=kind,limit=page.limit,digest=q.digest(values))
    start=0
    if page.cursor is not None:
        prior=history._decode(s,page.cursor,binding,domain=domain)
        if not isinstance(prior,dict) or set(prior)!=set(recipe)|{'last'} or type(prior['last']) is not int:
            raise history.invalid_guard()
        if {key:prior[key] for key in recipe}!=recipe:
            raise BookflowError('E_PREVIEW_STALE',details={'reason':'deposit_operation_page'})
        if prior['last']<0 or prior['last']>=len(values):raise history.invalid_guard()
        start=prior['last']+1
    selected=values[start:start+page.limit]
    cursor=history._encode(s,dict(recipe,last=start+len(selected)-1),binding,domain=domain) if start+len(selected)<len(values) else None
    return OperationPage(kind=kind,items=tuple(selected),total_count=len(values),digest=recipe['digest'],next_cursor=cursor)


def items(s,operation_key,kind,page,binding):
    kind=KINDS.validate_python(kind,strict=True)
    page=PageInput.model_validate_json(page.model_dump_json())
    saved=operations.find(s,operation_key)
    if saved is None:raise BookflowError('E_RECORD_NOT_FOUND')
    output=authorized_output(s,saved,binding)
    expected=collections(output).get(kind,[])
    stored=sorted(rows.rows(s,c.deposit_operation_items,c.deposit_operation_items.c.operation_id==saved['id'],c.deposit_operation_items.c.kind==kind),key=lambda v:v['ordinal'])
    if [v['ordinal'] for v in stored]!=list(range(len(expected))) or [json.loads(v['facts_snapshot']) for v in stored]!=expected:
        raise BookflowError('E_INTERNAL',message='Incomplete operation items.')
    return _page(s,expected,kind,page,binding,dict(operation=saved['id'],version=output.schema_version,company=s.company_row['id']),DOMAIN)


def preview_items(s,ctx,prepared,kind,page):
    from bookflow.company import deposit_coordination as coordination
    from bookflow.company.deposit_coordinate_persistence import build
    kind=KINDS.validate_python(kind,strict=True)
    page=PageInput.model_validate_json(page.model_dump_json())
    fresh=coordination.validate(s,ctx,prepared)
    output=build(s,ctx,fresh).output
    # Replace only explicit owner-typed generated references for stable preview
    # continuation. The final receipt retains the physical IDs.
    from bookflow.company.deposit_coordinate_validation import logical_collections
    values=logical_collections(output)[kind]
    return _page(s,values,kind,page,prepared.binding,
        dict(company=s.company_row['id'],fingerprint=prepared.facts_fingerprint,guard=q.digest(prepared.dependency_guard)),PREVIEW_DOMAIN)
