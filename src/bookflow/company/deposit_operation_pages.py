"""Private complete immutable operation collections and binding-owned pages."""
import json
from typing import Literal, Generic, TypeVar
from pydantic import TypeAdapter, JsonValue, ValidationError
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


Item=TypeVar('Item')


class PreviewPage(Frozen):
    """Logical-reference wire projection of validated per-kind typed items.

    Logical IDs and aggregate/at intentionally are not stored ID/date values;
    consumers must not parse this projection as an original operation receipt.
    """
    kind: Kind
    items: tuple[dict[str,JsonValue], ...]
    total_count: int
    digest: str
    next_cursor: str | None


class OperationPage(Frozen,Generic[Item]):
    kind: Kind
    items: tuple[Item, ...]
    total_count: int
    digest: str
    next_cursor: str | None


def item_type(kind,output):
    from bookflow.company.deposit_models import SourceRow,Additional,Cell
    from bookflow.company.deposit_lifecycle_models import MembershipChange,HeaderChange
    from bookflow.company.bank_effects import BankEffect
    from bookflow.company.payment_outputs import PaymentComponentOutput,PaymentApplicationOutput,PaymentAllocationOutput,InvoiceSettlementOutput
    from bookflow.company.deposit_coordinate_models import SalesComponentItem,SalesHeaderItem,SalesLineItem,SalesWorkItem,CoordinateCustomChange
    shared=dict(request_sources=SourceRow,request_additional=Additional,memberships=MembershipChange,
        document_changes=HeaderChange,cash_allocations=Cell,bank_changes=BankEffect)
    if kind in shared:return shared[kind]
    source=output.effect.source
    if source.payment_effect is not None:
        return dict(source_components=PaymentComponentOutput,source_applications=PaymentApplicationOutput,
            source_allocations=PaymentAllocationOutput,source_document_changes=InvoiceSettlementOutput)[kind]
    return dict(source_components=SalesComponentItem,source_applications=PaymentApplicationOutput,
        source_allocations=PaymentAllocationOutput,source_document_changes=SalesHeaderItem|SalesLineItem|SalesWorkItem|CoordinateCustomChange)[kind]


def typed_collections(output):
    coordinate=output.command=='deposit coordinate'
    effect=output.effect.deposit if coordinate else output.effect
    result=dict(request_sources=list(effect.financial.intent.sources),
        request_additional=list(effect.financial.intent.additional),
        memberships=list(effect.memberships),
        document_changes=list(effect.headers),
        cash_allocations=list(effect.financial.cells),
        bank_changes=list(effect.bank_effects))
    if not coordinate:return result
    source=output.effect.source
    if source.payment_effect is not None:
        owned=source.payment_effect.effect
        result.update(source_components=list(owned.source_components),
            source_applications=list(owned.applications),
            source_allocations=list(owned.allocations),
            source_document_changes=list(owned.document_changes))
    else:
        from bookflow.company.deposit_coordinate_models import SalesComponentItem, SalesHeaderItem, SalesLineItem, SalesWorkItem
        inserted=source.inserted
        lines={v.id:v for v in inserted.document_lines}
        components=[]
        for line in inserted.sales_line_profiles:
            components.append(SalesComponentItem(kind='sale_net',line_id=lines[line.document_line_id].line_id,
                tax_item_id=None,revision_id=line.revision_id,document_line_id=line.document_line_id,
                physical_component_id=None,capacity=line.net_minor_units))
        for tax in inserted.sales_tax_components:
            components.append(SalesComponentItem(kind='sale_tax',line_id=lines[tax.document_line_id].line_id,
                tax_item_id=tax.tax_item_id,revision_id=tax.revision_id,document_line_id=tax.document_line_id,
                physical_component_id=tax.id,capacity=tax.tax_minor_units))
        changes=[]
        if source.before_header!=source.after_header:
            changes.append(SalesHeaderItem(kind='header',before=source.before_header,after=source.after_header,
                revisions=inserted.transaction_revisions,profiles=inserted.sales_profiles,
                tax_attributions=inserted.sales_tax_attributions))
        for line in sorted(inserted.document_lines,key=lambda v:v.line_id):
            changes.append(SalesLineItem(kind='line',line=line,profile=next(v for v in inserted.sales_line_profiles if v.document_line_id==line.id),
                tax_components=tuple(v for v in inserted.sales_tax_components if v.document_line_id==line.id),
                tax_keys=tuple(v for v in inserted.sales_tax_attribution_lines if v.document_line_id==line.id)))
        for work in sorted(inserted.work_billing_allocations,key=lambda v:v.id):
            changes.append(SalesWorkItem(kind='work',allocation=work))
        changes.extend(sorted(source.custom_changes,key=lambda v:v.after.def_id))
        result.update(source_components=components,source_applications=[],source_allocations=[],source_document_changes=changes)
    return result


def collections(output):
    return {kind:[value.model_dump(mode='json') for value in values] for kind,values in typed_collections(output).items()}


def authorized_original(s,saved,binding,*,write=False):
    history.execution_binding(s,binding)
    indexed=rows.rows(s,c.deposit_operation_targets,c.deposit_operation_targets.c.operation_id==saved['id'])
    targets=tuple(sorted(v['transaction_id'] for v in indexed))
    # Admit the independently indexed/root evidence before inspecting corrupt
    # saved JSON. Completeness never becomes a hidden-history disclosure oracle.
    def admit(ids):
        try:
            history._authorize_binding_graph(s,binding,tuple(sorted(set(ids))),write=write)
        except BookflowError as error:
            if error.code=='E_PERMISSION':raise BookflowError('E_PERMISSION',details={}) from None
            raise
    admit((*targets,saved['transaction_id']))
    malformed=False
    resolved=[]
    try:
        request=json.loads(saved['request_snapshot'])
        resolved=request['resolved_transaction_ids']
        if type(resolved) is not list or any(type(v) is not str or not v for v in resolved):raise ValueError()
    except (ValueError,KeyError,TypeError):
        malformed=True;resolved=[]
    output=None
    try:
        output=operations.decode_output(saved['effect_snapshot'],saved['command'])
    except (ValueError,TypeError,ValidationError):
        malformed=True
    # Admit every recoverable root before diagnosing inconsistent evidence,
    # including effect roots when request JSON itself is corrupt. Equal roots
    # need no second check in this same binding/snapshot; this is no cross-read
    # permission cache and the next page starts with fresh admission.
    available=set(resolved)
    if output is not None and output.command=='deposit coordinate':available.update(output.effect.target_ids)
    admitted=set(targets)|{saved['transaction_id']}
    if available-admitted:admit((*admitted,*available))
    if malformed or not targets or saved['transaction_id'] not in targets or list(targets)!=sorted(resolved):
        raise BookflowError('E_INTERNAL',message='Incomplete operation evidence.')
    if output.operation_id!=saved['id'] or output.current.id!=saved['transaction_id']:
        raise BookflowError('E_INTERNAL')
    if output.command=='deposit coordinate' and output.effect.target_ids!=targets:raise BookflowError('E_INTERNAL')
    return output


def authorized_output(s,saved,binding,*,write=False):
    output=authorized_original(s,saved,binding,write=write)
    targets=output.effect.target_ids if output.command=='deposit coordinate' else ()
    updates=dict(current=operations.state(s,saved['transaction_id']))
    if output.command=='deposit coordinate':
        if output.effect.target_ids!=targets:raise BookflowError('E_INTERNAL')
        current={}
        for start in range(0,len(targets),200):
            for value in rows.rows(s,c.transactions,c.transactions.c.id.in_(targets[start:start+200])):
                current[value['id']]=CoordinateTransactionsRow.model_validate_json(q.canonical(value))
        if set(current)!=set(targets):raise BookflowError('E_INTERNAL')
        updates['current_headers']=tuple(current[identity] for identity in targets)
        from bookflow.company.deposit_coordinate_persistence import source_before
        updates['current_source_rows']=source_before(s,output.effect.source.before_header.id)
    return output.model_copy(update=updates)


def _page(s,values,kind,page,binding,recipe,domain,*,codec=None):
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
    model=OperationPage[codec] if codec is not None else PreviewPage
    return model.model_validate_json(q.canonical(dict(kind=kind,items=selected,total_count=len(values),digest=recipe['digest'],next_cursor=cursor)))


def items(s,operation_key,kind,page,binding):
    kind=KINDS.validate_python(kind,strict=True)
    page=PageInput.model_validate_json(page.model_dump_json())
    saved=operations.find(s,operation_key)
    if saved is None:raise BookflowError('E_RECORD_NOT_FOUND')
    output=authorized_original(s,saved,binding)
    expected=collections(output).get(kind,[])
    stored=sorted(rows.rows(s,c.deposit_operation_items,c.deposit_operation_items.c.operation_id==saved['id'],c.deposit_operation_items.c.kind==kind),key=lambda v:v['ordinal'])
    if [v['ordinal'] for v in stored]!=list(range(len(expected))) or [json.loads(v['facts_snapshot']) for v in stored]!=expected:
        raise BookflowError('E_INTERNAL',message='Incomplete operation items.')
    return _page(s,expected,kind,page,binding,dict(operation=saved['id'],version=output.schema_version,company=s.company_row['id']),DOMAIN,codec=item_type(kind,output))


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
    # Validate the full physical per-kind codec before the closed logical rewrite.
    for value in collections(output)[kind]:
        TypeAdapter(item_type(kind,output)).validate_json(q.canonical(value))
    values=logical_collections(output)[kind]
    return _page(s,values,kind,page,prepared.binding,
        dict(company=s.company_row['id'],fingerprint=prepared.facts_fingerprint,guard=q.digest(prepared.dependency_guard)),PREVIEW_DOMAIN)
