"""Unregistered customer freshness spike; complete SQL relation, bounded memory.

Uses current role admission and owning query expressions. No binding producer,
permission-policy activation, persistent digest, or runtime call site.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import time
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer

from bookflow.company import query_freshness as f
from bookflow.company.query import QueryInput, fingerprint
from bookflow.company.query_models import Descriptor
from bookflow.core.errors import BookflowError


class ProjectedItem(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    id: str
    version: int | None
    label: str
    active: bool
    projection_revision: str


class CustomerPage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query_contract_version: Literal[2] = 2
    query_fingerprint: str
    projection: Literal['summary', 'reference']
    items: list[ProjectedItem]
    count: int = Field(ge=0, le=200)
    next_cursor: str | None
    columns: list[Descriptor] | None = None
    matching_total: int | None = None

    @model_serializer(mode='wrap')
    def selected_only(self, handler):
        value = handler(self)
        if self.columns is None:
            value.pop('columns'); value.pop('matching_total')
        return value


@dataclass(frozen=True)
class CustomerRead:
    page: CustomerPage
    proof: f.QueryProof
    rows_hashed: int
    peak_partition: int
    scan_seconds: float
    dependency_rows: int
    encoded_bytes: int
    prepare_seconds: float


def _dependencies(inp, matching):
    """Additional predicate values, scoped to the complete matching customer set.

    Output/order cells are hashed by the primary stream. These source cells
    catch predicate edits even when the same rows still match (OR searches).
    Each relation is a set query, never a per-result graph walk.
    """
    from bookflow.company import schema as c
    from bookflow.commands import party_cmds as party
    t = c.customers
    matches = sa.select(matching.c.id)
    def owners(expression):
        return sa.select(expression).select_from(t).where(t.c.id.in_(matches))
    if inp.query and inp.query.strip():
        yield 'customer.search', sa.select(t.c.id, *(t.c[k] for k in ('full_name','company_name','account_number','notes'))).where(t.c.id.in_(matches)).order_by(t.c.id)
        contacts, points, addresses = c.customer_contacts, c.customer_contact_points, c.customer_addresses
        contact_owner = owners(party._customer_collection_owner('contact_mode'))
        fields = [contacts.c[k] for k in party.ContactOutput.model_fields if k not in {'id','role','points'}]
        yield 'customer.contacts.search', sa.select(contacts.c.id, *fields).where(contacts.c.customer_id.in_(contact_owner), contacts.c.active.is_(True)).order_by(contacts.c.id)
        yield 'customer.points.search', sa.select(points.c.id, points.c.custom_label, points.c.value).select_from(points.join(contacts, contacts.c.id==points.c.contact_id)).where(contacts.c.customer_id.in_(contact_owner),contacts.c.active.is_(True),points.c.active.is_(True)).order_by(points.c.id)
        yield 'customer.addresses.search', sa.select(addresses.c.id, addresses.c.label, *(addresses.c['address_'+k] for k in party.AddressOutput.model_fields)).where(addresses.c.customer_id.in_(owners(party._customer_collection_owner('address_mode'))),addresses.c.active.is_(True)).order_by(addresses.c.id)
        def billing_owner(a):
            return sa.case((sa.or_(*(a.c['billing_'+k].is_not(None) for k in party.AddressOutput.model_fields)),a.c.id))
        yield 'customer.billing.search', sa.select(t.c.id, *(t.c['billing_'+k] for k in party.AddressOutput.model_fields)).where(t.c.id.in_(owners(party._customer_inherited_expression('fresh_billing_owner',billing_owner)))).order_by(t.c.id)
        rep = sa.func.coalesce(party._customer_effective('job_sales_rep_id'),party._customer_effective('sales_rep_id'))
        for target, expr in ((c.customer_types,party._customer_effective('customer_type_id')),(c.job_types,t.c.job_type_id),(c.sales_reps,rep)):
            yield target.name+'.search', sa.select(target.c.id,target.c.full_name if 'full_name' in target.c else target.c.name).where(target.c.id.in_(owners(expr))).order_by(target.c.id)
    values, defs = c.custom_field_values, c.custom_field_defs
    wanted = [x.definition for x in inp.custom_filters]
    conditions = []
    if inp.query and inp.query.strip(): conditions.append(defs.c.kind.in_(('text','choice')))
    if wanted: conditions.append(values.c.def_id.in_(wanted))
    if conditions:
        yield 'customer.custom.predicates', sa.select(values.c.id,values.c.record_id,values.c.def_id,defs.c.kind,values.c.canonical_text).select_from(values.join(defs,defs.c.id==values.c.def_id)).where(values.c.record_type=='customer',values.c.record_id.in_(matches),values.c.active.is_(True),sa.or_(*conditions)).order_by(values.c.id)
    if wanted:
        yield 'customer.custom.definitions', sa.select(defs.c.id,defs.c.kind,defs.c.active).where(defs.c.id.in_(wanted)).order_by(defs.c.id)
        choices = c.custom_field_choices
        ids = [x.value for x in inp.custom_filters if x.kind=='choice']
        if ids:
            yield 'customer.custom.choices', sa.select(choices.c.id,choices.c.definition_id,choices.c.active,choices.c.value_key).where(choices.c.id.in_(ids)).order_by(choices.c.id)


def _prepare(inp, session):
    # Explicit customer recipe composed from the existing expression owners.
    from bookflow.company import query_providers as q, list_service
    from bookflow.company.query_catalog import selected_descriptors, error
    from bookflow.company.query_projection import configure, custom_predicates
    definition = q.get_list_definition('customer')
    if inp.columns is not None and inp.projection == 'reference':
        raise error('customer', 'Reference projections do not accept selected columns')
    descriptors = selected_descriptors('customer', inp.columns, session) if inp.columns is not None else None
    if inp.query and inp.query.strip():
        session.company.raw.create_function('bookflow_query_contains', -1, q.contains_any, deterministic=True)
    provider = q._provider('customer', inp, session)
    for predicate in ([provider.table.c.id.in_(inp.ids)] if inp.ids is not None else []) + (
            [custom_predicates('customer', inp.custom_filters, session, provider.table)] if inp.custom_filters else []):
        provider.visible = predicate if provider.visible is None else sa.and_(provider.visible, predicate)
    selected = dict(id=provider.table.c.id, version=provider.table.c.version,
                    active=provider.table.c.active, label=provider.table.c[definition.display_field])
    decoders = configure('customer', descriptors, provider, session) if descriptors is not None else {}
    if inp.projection == 'summary':
        names = [d.key for d in descriptors] if descriptors is not None else (definition.display_field, *definition.summary_columns)
        for name in dict.fromkeys(names):
            selected[name] = provider.columns.get(name, provider.table.c.get(name))
            if selected[name] is None:
                raise ValueError('Missing customer projection: ' + name)
        selected.update(provider.extra)
    statement = list_service.list_statement(provider.table, definition,
        query=None if provider.search_handled else inp.query,
        filters=inp.filter if provider.ordinary_filters is None else provider.ordinary_filters,
        sort=inp.sort, direction=inp.direction, include_inactive=inp.include_inactive,
        search_expressions=provider.search, filter_expressions=provider.filters,
        sort_expressions=provider.sorts, visible=provider.visible)
    # Preserve typed effective order values even when a change leaves row order fixed.
    for index, expression in enumerate(statement._order_by_clauses):
        while isinstance(expression, sa.sql.elements.UnaryExpression):
            expression = expression.element
        selected[f'__order_{index}'] = expression
    statement = statement.with_only_columns(*(v.label(k) for k, v in selected.items()), maintain_column_froms=True)
    if provider.batch is not None:
        raise ValueError('Customer recipe unexpectedly needs a new batch owner')

    def render(row):
        # Same owning transformations as query_page, including exact AR money.
        if inp.projection == 'summary':
            if provider.transform is not None:
                row = provider.transform(row)
            if descriptors is not None:
                return {**{k: row[k] for k in ('id', 'version', 'label', 'active')}, 'values': {
                    d.key: decoders[d.key](row[d.key], row) if d.key in decoders else row[d.key] for d in descriptors}}
            allowed = {'id', 'version', 'label', 'active', definition.display_field, *definition.summary_columns}
        else:
            allowed = {'id', 'version', 'label', 'active'}
        return {k: v for k, v in row.items() if k in allowed}
    return statement, provider.parameters, descriptors, render


def _stream(inp, statement, dialect):
    """One shared complete matching CTE, never repeated matching subqueries.

    UNION payloads have no SQLAlchemy result processors: each arm restores its
    own typed columns, avoiding e.g. a primary bool processor on contact text.
    """
    ordering = tuple(statement._order_by_clauses)
    matching = statement.order_by(None).add_columns(
        sa.func.row_number().over(order_by=ordering).label('__position')
    ).cte('query_customer_matching').prefix_with('MATERIALIZED')
    dependencies = list(_dependencies(inp, matching))
    if not dependencies:
        sources = [('customer.rows', statement)]
        combined = statement
    else:
        primary = sa.select(*(matching.c[col.key] for col in statement.selected_columns)).order_by(matching.c.__position)
        sources = [('customer.rows', primary), *dependencies]
        width = max(len(stmt.selected_columns) for _, stmt in sources)
        arms = []
        for tag, (_, stmt) in enumerate(sources):
            position = (matching.c.__position if tag == 0 else
                        sa.func.row_number().over(order_by=tuple(stmt._order_by_clauses)))
            columns = [sa.type_coerce(col, sa.types.NullType()).label('cell_'+str(i))
                       for i, col in enumerate(stmt.selected_columns)]
            columns += [sa.null().label('cell_'+str(i)) for i in range(len(columns), width)]
            arms.append(stmt.order_by(None).with_only_columns(
                sa.literal(tag).label('stream'), position.label('position'), *columns,
                maintain_column_froms=True))
        combined = sa.union_all(*arms).order_by(sa.column('stream'), sa.column('position'))
    formats = []
    for owner, stmt in sources:
        cols = tuple(stmt.selected_columns)
        processors = tuple(col.type._cached_result_processor(dialect, None) for col in cols)
        formats.append((owner, tuple(str(col.key) for col in cols), processors, f.flat_encoder(len(cols))))
    return combined, formats, bool(dependencies)


def read_customer(session, inp: QueryInput) -> CustomerRead:
    """Role-only private read. Future F1 binding is deliberately not fabricated."""
    from bookflow.hub.access import company_role, role_satisfies
    from bookflow.company.ledger_reports import _snapshot
    from bookflow.company.query_sql import execute
    # This unregistered role-only experiment consumes the actual role resolver;
    # it is not a new conditional resource owner or a granular admission route.
    access, role = company_role(session, session.company_row['id'], session.company_row['organization_id'])
    if access is None:
        raise BookflowError('E_COMPANY_NOT_FOUND')
    if not role_satisfies(role, access, 'member', session.is_hub_admin):
        raise BookflowError('E_PERMISSION')
    if session.actor is None or session.actor.kind != 'human':
        raise BookflowError('E_PERMISSION', details={'reason': 'query_binding_not_integrated'})
    try:
        with _snapshot(session.company):
            key = f.cursor_key(session.company)
            access, role = company_role(session, session.company_row['id'], session.company_row['organization_id'])
            mask = f.role_mask(session, 'customer')
            audience = f.keyed(key, f.FACTS, ('role-only', session.actor.id, access, role,
                                             session.is_hub_admin, mask.partial, mask.hidden))
            identity = dict(company=str(session.company_row['id']), contract=fingerprint(inp), audience=audience)
            previous = f.decode_cursor(key, inp.cursor) if inp.cursor else None
            if previous and previous.proof.model_dump(exclude={'facts', 'owner'}) != identity:
                raise f.invalid_cursor()
            offset = previous.offset if previous else 0
            prepare_started = time.perf_counter()
            statement, parameters, descriptors, render = _prepare(inp, session)
            metadata = [d.model_dump(mode='json') for d in descriptors] if descriptors is not None else None
            stream, formats, tagged = _stream(inp, statement, session.company.conn.dialect)
            # Recipe 2 commits typed scalar source cells with the owning decode
            # contract/presentation, rendering public nested objects only on-page.
            # Customer's current role view is whole; partial recipes must project
            # masked cells BEFORE this stream and cannot inherit this declaration.
            presentation = ((session.actor.timezone or session.company_tz) if descriptors and
                            any(d.key in ('created_at', 'updated_at') for d in descriptors) else None)
            digest = hmac.new(key, f.FACTS + f.canonical(('customer-recipe-2', identity, metadata,
                presentation, session.company_info_row['home_currency'],
                [(owner, names) for owner, names, _, _ in formats])), 'sha256')
            count, peak, selected, dependency_rows, encoded_bytes = 0, 0, [], 0, 0
            started = time.perf_counter()
            names = formats[0][1]
            balance_positions = [i for i, name in enumerate(names) if name in ('current_balance', 'open_balance')]
            from bookflow.core.exact import _require_i64
            with execute(session.company.conn, stream, parameters) as result:
                for partition in result.partitions(f.PARTITION):
                    peak = max(peak, len(partition))
                    for source in partition:
                        tag = source[0] if tagged else 0
                        owner, keys, processors, encode = formats[tag]
                        cells = tuple(source[2:2+len(keys)]) if tagged else tuple(source)
                        if tagged:
                            cells = tuple(process(value) if process else value for process, value in zip(processors, cells))
                        if tag == 0:
                            for pos in balance_positions:
                                _require_i64(int(cells[pos]), field=names[pos])
                        digest.update(tag.to_bytes(2, 'big'))
                        encoded = encode(cells)
                        encoded_bytes += len(encoded) + 2
                        digest.update(encoded)
                        if tag == 0:
                            if offset <= count < offset + inp.limit:
                                row = render(dict(zip(names, cells)))
                                selected.append(f.project_record(key, audience, 'customer', row, mask))
                            count += 1
                        else:
                            dependency_rows += 1
            digest.update(f.canonical(('matching_total', count)))
            proof = f.QueryProof(**identity, facts=digest.hexdigest())
            if previous:
                f.check_proof(previous.proof, proof)
                if offset > count:
                    raise f.invalid_cursor()
            page = CustomerPage(query_fingerprint=proof.facts, projection=inp.projection,
                items=[ProjectedItem.model_validate(row) for row in selected], count=len(selected),
                next_cursor=f.continuation(key, proof, offset, len(selected), count),
                columns=descriptors, matching_total=count if descriptors is not None else None)
            return CustomerRead(page, proof, count, peak, time.perf_counter() - started, dependency_rows,
                                encoded_bytes, started - prepare_started)
    except sa.exc.SQLAlchemyError:
        raise BookflowError('E_IO', details={'reason': 'query_facts_unavailable'}) from None
