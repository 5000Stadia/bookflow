"""Public deposit read producers: admission, private financial reuse, projection.

The private readers in ``deposit_queries`` own admission, financial validation
and continuation mechanics; nothing here recomputes them. This module adds the
public audience decisions and builds every wire field individually, following
the closed dispositions in ``deposit_public_manifest``.
"""
from bookflow.company import deposit_queries as q
from bookflow.company import deposit_read_authority as authority
from bookflow.company import deposit_read_facts as facts
from bookflow.company import deposit_read_models as m
from bookflow.company import deposit_read_pages as pages
from bookflow.company import deposit_dependency_history as history_owner
from bookflow.company import deposit_models as dm
from bookflow.company import deposit_public_authority as pa
from bookflow.company import deposit_public_manifest as manifest
from bookflow.company import deposit_public_models as w
from bookflow.company.deposit_dependency_models import InspectionRoot
from bookflow.core.errors import BookflowError

# A distinct continuation domain over the accepted company signing material.
#
# G1 key provenance and its accepted limitation. The signing key is the company's
# own `report_cursor_keys` row (`ledger_reports._cursor_key`), created once with
# the company and read from the company database; this module creates no key and
# holds none. `deposit_read_pages._scope` binds every public items token to that
# key, to this domain string, to the company id and to the executing actor,
# actor kind, principal and current authority epoch. A token therefore cannot be
# replayed under the private `items` domain, under another company, or by another
# subject, and it stops verifying the moment the principal binding epoch changes.
#
# The limitation, unchanged by this stage and registered as D3: that key has no
# rotation. There is no command to roll it, no key id in the token, and no second
# key to verify against, so a leaked key stays valid for the life of the company
# and rotation would invalidate every outstanding continuation at once. Public
# items cursors inherit that exposure exactly as the existing private cursors do.
# Rotation belongs to its own allocation; nothing here should be read as closing
# it.
PURPOSE = 'public.items'

# Unknown-history entry prefixes that name an optional master reference.
UNKNOWN_REFERENCE_TABLES = {kind: table for table, (kind, _) in pa.REFERENCE_CAPABILITIES.items()}


def _internal(reason):
    return BookflowError('E_INTERNAL', message='deposit public projection: ' + reason)


# ------------------------------------------------------------------ admission


def open_selected(reader, audience, company, ctx):
    """Governed admission precedes selected-company filesystem and record lookup."""
    from bookflow.core.dispatch import resolve_company, open_company
    audience.require(company)
    session = reader.session
    session.company_row = resolve_company(session, company, 'option')
    open_company(session, ctx, False)
    reader.authenticate()
    return session


BATCH = 200


def graph_requirements(s, evidence):
    """Complete current requirements of the admitted connected graph.

    Reuses the registered payment/work owners exactly as the private legacy
    checks do; it introduces no permission vocabulary of its own. The roots,
    events and drafts come from the closure, never from a displayed page, so
    off-page dependencies are covered.
    """
    from bookflow.company import payment_authority as authority_owner
    required = set()
    roots = sorted(set(evidence.roots))
    for start in range(0, max(1, len(roots)), BATCH):
        required.update(authority_owner.requirements(s.company, roots[start:start + BATCH]))
    events = sorted(set(evidence.events))
    for start in range(0, len(events), BATCH):
        cohort = events[start:start + BATCH]
        reader = authority_owner._EventCohort(s.company, cohort)
        for event in cohort:
            required.update(reader.requirements(event))
        del reader
    if evidence.drafts:
        required.add(pa.READ_REQUIREMENT)
    return tuple(sorted(required))


def _require_graph(s, audience, evidence):
    """The public granular boundary the retained legacy checks cannot supply.

    hub.access.require_resource decides on legacy company roles and never applies
    granular capability denies, so every requirement of the connected graph is
    re-asked of the current actor/principal intersection here, before any
    financial decode. Denial answers with the private reader's exact-read
    non-disclosing not-found, never a distinguishable code or detail.
    """
    for capability, threshold in graph_requirements(s, evidence):
        if not audience.admits(capability, threshold):
            raise BookflowError('E_RECORD_NOT_FOUND')


def _admit(s, audience, deposit):
    """Reauthorize this request, then take the private non-disclosing exact read.

    Company capability denial answers E_PERMISSION like every other registered
    command; a record inside a denied aggregate keeps the private reader's
    non-disclosing E_RECORD_NOT_FOUND.
    """
    manifest.conform()
    pa.conform()
    binding = audience.binding()
    audience.require(s.company_row['id'])
    evidence = authority.selected(s, deposit, binding=binding)
    _require_graph(s, audience, evidence)
    return binding, evidence


def _inspection_status(s, data, binding, audience):
    """Audience-safe projected history status; the private guard stays internal.

    ``BaselineRecipe`` is never issued, encoded or hashed here: its read digest
    derives from readset anchors this reader may not see. Unknown history about
    a reference group this reader is denied does not change the public status.
    """
    try:
        _, readset = history_owner.capture(s, InspectionRoot(kind='deposit', id=data.header['id']), binding)
    except history_owner.MissingHistory:
        return 'unknown_history'
    for entry in readset.unknown:
        kind, separator, identity = entry.partition(':')
        table = UNKNOWN_REFERENCE_TABLES.get(kind) if separator and identity else None
        if table is None or audience.reference_admitted(table):
            return 'unknown_history'
    return 'complete'


# ------------------------------------------------------------- reference groups


def _money(value):
    return w.Money(minor_units=value.minor_units, currency=value.currency)


def _amount(units, currency):
    return w.Money(minor_units=units, currency=currency)


def _account(captured, audience):
    if not audience.reference_admitted('accounts'):
        return w.AccountReference(disclosed=False, id=None, name=None, full_name=None, number=None,
                                  account_type=None, normal_balance=None, system_role=None,
                                  active=None, currency=None)
    return w.AccountReference(disclosed=True, id=captured.id, name=captured.name,
                              full_name=captured.full_name, number=captured.number,
                              account_type=captured.type, normal_balance=captured.normal_balance,
                              system_role=captured.system_role, active=captured.active,
                              currency=captured.currency)


def _source_account(captured, audience):
    if not audience.reference_admitted('accounts'):
        return w.SourceAccountReference(disclosed=False, id=None, name=None, full_name=None,
                                        number=None, account_type=None, normal_balance=None)
    return w.SourceAccountReference(disclosed=True, id=captured.id, name=captured.name,
                                    full_name=captured.full_name, number=captured.number,
                                    account_type=captured.type, normal_balance=captured.normal_balance)


def _party(kind, identity, label, version, audience):
    if kind not in pa.PARTY_TABLES:
        raise _internal('unknown party kind')
    if not audience.party_admitted(kind):
        return w.PartyReference(group=kind, disclosed=False, id=None, label=None, version=None)
    return w.PartyReference(group=kind, disclosed=True, id=identity, label=label, version=version)


def _class(identity, label, audience):
    if not audience.reference_admitted('classes'):
        return w.ClassReference(disclosed=False, id=None, label=None)
    return w.ClassReference(disclosed=True, id=identity, label=label)


def _method(reference, audience):
    if reference is None:
        return None
    if not audience.reference_admitted('payment_methods'):
        return w.PaymentMethodReference(disclosed=False, id=None, label=None, version=None)
    return w.PaymentMethodReference(disclosed=True, id=reference.id, label=reference.label,
                                    version=reference.version)


def _issuer(captured, audience):
    names = tuple(k for k in w.IssuerIdentity.model_fields if k not in ('group', 'disclosed'))
    if not audience.issuer_admitted():
        return w.IssuerIdentity(disclosed=False, **{name: None for name in names})
    return w.IssuerIdentity(disclosed=True, **{name: getattr(captured, name) for name in names})


def _custom_fields(values, audience):
    admitted = audience.custom_fields_admitted()
    result = []
    for value in values:
        if not admitted:
            result.append(w.CustomFieldValue(disclosed=False, definition_id=None, definition_version=None,
                                             name=None, kind=None, value=None, canonical_text=None,
                                             position=None, choice_id=None, choice_label=None,
                                             print_visibility=None))
            continue
        captured = value.captured
        result.append(w.CustomFieldValue(disclosed=True, definition_id=captured.definition_id,
                                         definition_version=captured.definition_version,
                                         name=captured.name, kind=captured.kind, value=captured.value,
                                         canonical_text=captured.canonical_text, position=captured.position,
                                         choice_id=captured.choice_id, choice_label=captured.choice_label,
                                         print_visibility=value.captured_print_visibility))
    return tuple(result)


def _current_references(navigation, named, audience):
    """Only the masters this response actually names, and only admitted groups.

    ``named`` is the closed set of (navigation kind, identity) pairs the emitted
    summary or page rows reference, so neither surface grows with the whole
    connected graph.
    """
    result = []
    for row in navigation:
        group = pa.REFERENCE_GROUPS.get(row.kind)
        if group is None:
            raise _internal('unknown navigation kind')
        if (row.kind, row.id) not in named or not audience.reference_admitted(row.kind):
            continue
        result.append(w.CurrentReference(group=group, id=row.id, label=row.label, active=row.active,
                                         version=row.version, current_type=row.current_type,
                                         available=row.available))
    return tuple(result)


def _name(named, table, reference):
    """Record one emitted reference; a redacted group names nothing."""
    if reference is not None and reference.disclosed and reference.id is not None:
        named.add((table, reference.id))


def _named_by_summary(header):
    """Bank and cash-back accounts: every master the show summary itself names."""
    named = set()
    _name(named, 'accounts', header.deposit_to)
    if header.cash_back is not None:
        _name(named, 'accounts', header.cash_back.account)
    return named


def _named_by_rows(rows):
    """Masters the emitted composition rows name; bounded by the page limit."""
    named = set()
    for row in rows:
        if row.row == 'source':
            _name(named, 'accounts', row.from_account)
            _name(named, pa.PARTY_TABLES[row.payer.group], row.payer)
            _name(named, 'payment_methods', row.payment_method)
            for party in row.allocation_parties:
                _name(named, pa.PARTY_TABLES[party.group], party)
            for grouping in row.allocation_classes:
                _name(named, 'classes', grouping)
        elif row.row == 'additional':
            _name(named, 'accounts', row.account)
            if row.party is not None:
                _name(named, pa.PARTY_TABLES[row.party.group], row.party)
            _name(named, 'classes', row.class_reference)
            _name(named, 'payment_methods', row.payment_method)
    return named


# ------------------------------------------------------------------- item rows


def _unique(values):
    """Collapse redacted duplicates; a denied group never reveals how many it hid."""
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _source_row(item, deposit_id, audience):
    captured = item.captured
    source = captured.source
    profile = source.profile
    payer = profile.payer if source.source_type == 'payment' else profile.customer
    parties = []
    classes = []
    for component in source.components:
        cash = component.cash
        if cash.party_id is not None:
            parties.append(_party(cash.party_kind, cash.party_id, cash.party_name, None, audience))
        if cash.class_id is not None:
            classes.append(_class(cash.class_id, cash.class_name, audience))
    current = item.current
    return w.SourceRow(
        row_id=captured.row_id, ordinal=captured.ordinal,
        source=w.SourcePin(source_type=source.source_type, transaction_id=source.transaction_id,
                           revision_id=source.revision_id,
                           expected_header_version=source.expected_header_version,
                           number=item.source_number),
        receipt_date=source.receipt_date,
        amount=_amount(source.cash_minor_units, source.currency),
        memo=captured.memo, memo_origin=captured.memo_origin, source_memo=source.source_memo,
        check_reference=source.source_reference,
        payer=_party('customer', payer.id, payer.label, payer.version, audience),
        from_account=_source_account(item.from_account, audience),
        payment_method=_method(profile.payment_method, audience),
        allocation_parties=_unique(parties), allocation_classes=_unique(classes),
        components=tuple(w.SourceComponent(ordinal=row.ordinal, kind=row.key.kind, present=row.present)
                         for row in captured.occurrences),
        current=w.SourceCurrentState(
            transaction_id=current.id, status=current.status, version=current.version,
            revision_id=current.revision_id, claimed=current.claimed_by is not None,
            claimed_by_this_deposit=current.claimed_by == deposit_id,
            payment_method_type=(current.payment_method_type
                                 if audience.reference_admitted('payment_methods') else None)))


def _additional_row(row, currency, audience):
    dimensions = row.dimensions
    return w.AdditionalRow(
        row_id=row.row_id, ordinal=row.ordinal, amount=_amount(row.units, currency), memo=row.memo,
        check_number=row.check_number, account=_account(row.account, audience),
        party=(_party(dimensions.party_kind, dimensions.party_id, dimensions.party_name, None, audience)
               if dimensions.party_id is not None else None),
        class_reference=(_class(dimensions.class_id, dimensions.class_name, audience)
                         if dimensions.class_id is not None else None),
        payment_method=_method(row.payment_method, audience))


def _allocation_row(item, currency):
    captured = item.captured
    bucket = captured.bucket
    if bucket in ('main_bank', 'cash_back'):
        name, additional = bucket, None
    elif bucket.startswith('additional:') and len(bucket) > len('additional:'):
        name, additional = 'additional', bucket[len('additional:'):]
    else:
        raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
    return w.AllocationRow(row_id=captured.row_id, component_ordinal=captured.component_ordinal,
                           bucket=name, additional_row_id=additional,
                           amount=_amount(captured.units, currency))


def _row(value, deposit_id, currency, audience):
    if type(value) is m.SourceItem:
        return _source_row(value, deposit_id, audience)
    if type(value) is dm.Additional:
        return _additional_row(value, currency, audience)
    if type(value) is m.CellItem:
        return _allocation_row(value, currency)
    raise _internal('unknown composition row')


# --------------------------------------------------------------- shared header


def _totals(totals):
    return w.DepositTotals(**{name: _money(getattr(totals, name)) for name in m.Totals.model_fields})


def _counts(counts):
    return w.DepositCounts(**{name: getattr(counts, name) for name in m.Counts.model_fields})


def _pin(pin):
    return w.DepositPin(deposit_id=pin.deposit, revision_id=pin.revision_id,
                        revision_number=pin.revision_number)


def _current_state(state):
    currency = state.currency
    return w.CurrentState(
        deposit_id=state.id, version=state.version, revision_id=state.revision_id, number=state.number,
        status=state.status, revision_date=state.revision_date,
        revision_posting_total=_amount(state.revision_posting_total, currency),
        revision_subtotal=_amount(state.revision_subtotal, currency),
        revision_bank_total=_amount(state.revision_bank_total, currency),
        revision_cash_back=_amount(state.revision_cash_back, currency),
        effective_bank_total=_amount(state.effective_bank_total, currency),
        active_source_count=len(state.active_source_ids))


def _selected_header(selected, currency, audience):
    cash_back = None
    if selected.cash_back is not None:
        cash_back = w.CashBackLine(amount=_amount(selected.cash_back.units, currency),
                                   memo=selected.cash_back.memo,
                                   account=_account(selected.cash_back.account, audience))
    return w.SelectedHeader(pin=_pin(selected.pin), date=selected.date, number=selected.number,
                            memo=selected.memo, deposit_to=_account(selected.deposit_to, audience),
                            cash_back=cash_back,
                            custom_fields=_custom_fields(selected.custom_fields, audience),
                            issuer=_issuer(selected.issuer, audience))


def _revisions(data, private):
    numbers = {value.pin.revision_id: number for number, value in data.selected.items()}
    current = data.header['current_revision_id']
    selected = private.selected.pin.revision_id
    result = []
    for link in private.links:
        if link.kind != 'revision' or link.id not in numbers:
            continue
        result.append(w.RevisionLink(revision_number=numbers[link.id], revision_id=link.id,
                                     selected=link.id == selected, current=link.id == current))
    return tuple(sorted(result, key=lambda row: row.revision_number))


def _annotation_links(private, access):
    result = []
    for link in private.links:
        if link.kind == 'note' and access['notes'] == 'available':
            result.append(w.AnnotationLink(kind='note', id=link.id, attachment_id=None, active=link.active))
        elif link.kind == 'attachment' and access['attachments'] == 'available':
            result.append(w.AnnotationLink(kind='attachment', id=link.id, attachment_id=link.related_id,
                                           active=link.active))
    return tuple(result)


def _dated(state):
    if state is None:
        return None
    return w.DatedFinancialState(as_of=state.as_of, basis=state.basis,
                                 knowledge_observed_at=state.knowledge_observed_at,
                                 cutoff_after_evaluation_date=state.cutoff_after_evaluation_date,
                                 financial_state=state.financial_state,
                                 bank_movement=_money(state.bank_movement),
                                 source_membership_total=_money(state.source_membership_total))


# ------------------------------------------------------------------- producers


def show(s, inp, *, audience, at=None):
    """Bounded public detail for one deposit revision; composition stays on items.

    ``at`` pins the observation instant. The publication owner re-executes this
    producer at the instant its proof captured, so a release compares the same
    document rather than refusing because the clock moved.
    """
    inp = q.checked(inp, m.ShowInput)
    binding, evidence = _admit(s, audience, inp.deposit)
    # G3: the capability decision precedes association access, so a denied
    # annotation kind is never acquired, not merely dropped from the result.
    access = audience.annotation_access()
    acquire = tuple(name[:-1] for name, value in sorted(access.items()) if value == 'available')
    data = facts.load_complete(s, [inp.deposit], binding=binding, annotations=acquire)[0]
    private = q._show(s, data, inp, binding, with_guard=False, at=at)
    history = _inspection_status(s, data, binding, audience)
    audience.validate()
    header = _selected_header(private.selected, private.currency, audience)
    return w.DepositDetail(
        company_id=private.company_id, deposit_id=private.selected.pin.deposit, currency=private.currency,
        selected=header,
        selected_is_current=private.selected_is_current,
        current=_current_state(private.current), current_observed_at=private.current_observed_at,
        totals=_totals(private.totals), counts=_counts(private.counts),
        dated_state=_dated(private.dated_state),
        inspection=w.InspectionSummary(purpose=private.dependencies.purpose, history=history,
                                       source_count=len(private.dependencies.source_ids)),
        annotations=w.AnnotationAccess(**access),
        revisions=_revisions(data, private),
        links=_annotation_links(private, access),
        current_references=_current_references(private.current_references,
                                              _named_by_summary(header), audience))


def items(s, inp, *, audience, at=None):
    """One page of the selected revision's composition, over the disclosed relation.

    ``at`` pins the observation instant, for the same reason it does on `show`.
    """
    inp = q.checked(inp, m.ItemsInput)
    binding, evidence = _admit(s, audience, inp.deposit)
    number = inp.revision_number
    if inp.page.cursor:
        position = pages.decode(s, binding, PURPOSE, inp.page.cursor)['position']
        if (type(position) is not list or len(position) != 2 or type(position[0]) is not int
                or (number is not None and number != position[0])):
            raise BookflowError('E_VALIDATION', details={'field': 'cursor'})
        number = position[0]
    # No page emits an annotation link, so no association is ever acquired here.
    data = facts.load_complete(s, [inp.deposit], binding=binding, annotations=())[0]
    selected = q.selected(data, number)
    effect = data.effects[selected.pin.revision_id]
    currency = effect.intent.currency
    values = q.collections(data, selected.pin)[inp.kind]
    rows = tuple(_row(value, data.header['id'], currency, audience) for value in values)
    content = [selected.pin.model_dump(mode='json'), inp.kind,
               [row.model_dump(mode='json') for row in rows]]
    chunk, fingerprint, following, _ = pages.page(s, binding, PURPOSE, rows, content, inp.page.limit,
                                                  inp.page.cursor, pin=selected.pin.revision_number)
    audience.validate()
    return w.DepositItemsPage(
        company_id=s.company_row['id'], deposit_id=data.header['id'], selected=_pin(selected.pin),
        kind=inp.kind, items=chunk, total_count=len(rows), totals=_totals(q.totals(effect)),
        fingerprint=fingerprint, next_cursor=following, current=_current_state(q.current(data)),
        current_observed_at=at or q.now(),
        current_references=_current_references(data.references, _named_by_rows(chunk), audience))


def _query_admitted(s, identity, audience, binding):
    """Only an evaluated aggregate requirement denial means an omitted deposit.

    Exact-read selected() normalizes unresolved evidence to not-found. A complete
    query cannot interpret that normalization as permission to publish partial totals.
    """
    try:
        evidence = authority.admit(s, [identity], binding=binding)
        requirements = graph_requirements(s, evidence)
    except BookflowError as error:
        if error.code in ('E_PERMISSION', 'E_RECORD_NOT_FOUND'):
            raise BookflowError('E_DEPOSIT_SOURCE_INVALID') from None
        raise
    return all(audience.admits(capability, threshold) for capability, threshold in requirements)


def _received_from(effect, audience):
    parties = []
    for row in effect.intent.sources:
        profile = row.source.profile
        payer = profile.payer if row.source.source_type == 'payment' else profile.customer
        parties.append(_party('customer', payer.id, payer.label, payer.version, audience))
    for row in effect.intent.additional:
        d = row.dimensions
        if d.party_id is not None:
            parties.append(_party(d.party_kind, d.party_id, d.party_name, None, audience))
    return _unique(parties)


def query(s, inp, *, audience, at=None):
    """Complete public matching relation; one financial load, never show per row."""
    import sqlalchemy as sa
    from bookflow.company import schema as c
    inp = q.checked(inp, m.QueryInput)
    manifest.conform()
    binding = audience.binding()
    audience.require(s.company_row['id'])
    authority.authenticate(s, binding)
    if inp.status == 'deleted' or (inp.status is None and inp.include_deleted):
        raise BookflowError('E_VALIDATION', details={'reason':'feature_unavailable','feature':'transaction_deleted'})
    bank = None
    if inp.deposit_to:
        # Optional bank disclosure does not govern the deposit's financial admission.
        # An explicit filter, however, may not resolve an unavailable reference.
        if not audience.reference_admitted('accounts'):
            raise BookflowError('E_PERMISSION')
        from bookflow.company.accounts import resolve_account
        try:
            bank = resolve_account(s.company, inp.deposit_to)['id']
        except BookflowError as unresolved:
            # The list-selector refusal carries open `suggestions`, which a closed
            # public failure cannot hold. Released unnarrowed it leaves the captured
            # outcome path altogether and the reader is told, wrongly, that they may
            # not run this command: a mistyped bank name reported as a denial.
            if unresolved.code not in ('E_RECORD_NOT_FOUND', 'E_VALIDATION'):
                raise
            raise BookflowError(unresolved.code, details={'field': 'deposit_to'}) from None
    identities = list(s.company.conn.execute(sa.select(c.transactions.c.id).where(
        c.transactions.c.type == 'deposit').order_by(c.transactions.c.id)).scalars())
    allowed = [identity for identity in identities if _query_admitted(s, identity, audience, binding)]
    matches = []
    currency = s.company_info_row['home_currency']
    for data in facts.load_complete(s, allowed, binding=binding, annotations=()):
        selected = q.selected(data)
        effect = data.effects[selected.pin.revision_id]
        private = m.DepositRow(selected=selected, current=q.current(data), totals=q.totals(effect), counts=q.counts(effect))
        row = w.DepositQueryRow(selected=_selected_header(selected, currency, audience),
            current=_current_state(private.current), totals=_totals(private.totals), counts=_counts(private.counts),
            received_from=_received_from(effect, audience))
        text = ' '.join([row.selected.number, row.selected.memo or ''] +
                        [party.label for party in row.received_from if party.disclosed and party.label]).casefold()
        if bank and row.selected.deposit_to.id != bank: continue
        if inp.status and row.current.status != inp.status: continue
        if inp.date_from and row.selected.date < inp.date_from: continue
        if inp.date_to and row.selected.date > inp.date_to: continue
        if inp.number is not None and row.selected.number != inp.number: continue
        if inp.q and inp.q.casefold() not in text: continue
        matches.append((row, private))
    matches.sort(key=lambda pair: ({'date':pair[0].selected.date, 'number':pair[0].selected.number,
        'bank_total':pair[0].totals.bank_total.minor_units}[inp.sort], pair[0].current.deposit_id),
        reverse=inp.direction == 'desc')
    totals, effective = q.query_totals([private for _, private in matches], currency)
    values = [row for row, _ in matches]
    # Include defaults, sort and direction even when the caller omitted them.
    # Page size stays adjustable under the existing offset/previous-page rules.
    contract = inp.model_dump(mode='json', exclude={'page'})
    chunk, fingerprint, following, previous = pages.page(s, binding, 'public.query', values,
        [contract, [row.model_dump(mode='json') for row in values]], inp.page.limit, inp.page.cursor)
    audience.validate()
    return w.DepositQueryPage(company_id=s.company_row['id'], currency=currency, items=chunk,
        total_count=len(values), totals=_totals(totals), effective_bank_total=_money(effective),
        fingerprint=fingerprint, next_cursor=following, previous_cursor=previous)
