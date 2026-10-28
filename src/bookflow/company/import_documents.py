"""Document imports (R107 increment 4, design/bulk-import.md §2, §11): rows become invoices, bills, payments...

A **document kind** (``import_kinds.DOC_KINDS``) names the registry command that posts one document. Rows sharing the
group key (an invoice's number; a bill's vendor and reference; a payment's customer, date, amount and reference) are
one document: its **header** fields come from the first row that fills each, and a later row that fills one
differently refuses the whole document (``header_conflict``); each row that fills any line field is one **line**, in
file order. A row whose key cells are all blank continues the document above it (a file that writes the number only
on a document's first row). With no key mapped, each row is its own document, **keyed by content**, and a real run
needs ``key_by_content``: an edited amount would otherwise import a second copy.

The command takes references as the file's text and resolves them itself; the check looks each up first, in memory,
so an unknown customer, item or account is named before anything runs (customers and vendors may be created on
request, never an item or account). A payment is **applied by number**: each row's invoice is found by its number,
must be the payment's customer's and open, and the run sends its current version. A bill payment names bills by
number the same way. What reaches the command is what a person would type, so the books, postings and error codes
are the typed document's (criterion 5); rounding is the command's own.

Documents are created, never matched or updated: an import's own documents are found again through the key ledger
(`already_in`), and a number the books already use is refused as the command would (`E_DUPLICATE_NUMBER`). Reversal
retires each document through its family's delete, with history preserved (else its void), and is refused while a
payment is applied to it, naming the payment (``batches``).
"""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import import_kinds as kinds, schema as c, tabular

# Document references found by number, not by a list's name: an application's invoice, a bill payment's bill.
DOCUMENT_TARGETS = {'invoice': ('invoice',), 'bill': ('bill',)}
# Number series the commands share (document_effects.NUMBER_FAMILIES): a credit memo takes an invoice number.
NUMBER_FAMILIES = {'invoice': ('invoice', 'credit_memo'), 'credit_memo': ('invoice', 'credit_memo')}


def _imports():
    from bookflow.company import imports
    return imports


# ------------------------------------------------------------------ rows into documents

def plan_documents(kind: kinds.DocKind, table: tabular.Table, mapping, source: str, places: int,
                   year: int) -> list:
    """The file's documents, each a ``RowPlan`` whose ``spans`` are the file rows it holds."""
    imp = _imports()
    specs = kinds.fields(kind.name)
    column_of = {p: table.headers[i] for i, p in mapping.columns.items()}
    position = {p: i for i, p in mapping.columns.items()}
    docs: list = []
    by_key: dict[str, Any] = {}
    occurrences: dict[str, int] = {}
    current = None
    for row_no, (line, cells) in enumerate(table.rows, start=1):
        scratch = imp.RowPlan(row_no, line, cells)
        if row_no in table.width_errors:
            message = f'The row has {len(cells)} cells; the header has {len(table.headers)}.'
            scratch.problems.append({'column': None, 'code': 'row_width', 'value': '', 'message': message,
                                     'row': row_no})
            doc = _new_doc(imp, len(docs) + 1, row_no, line)
            docs.append(doc)
            _absorb(doc, scratch, {}, row_no, line, cells)
            continue
        values = imp.read_cells(kind, table, mapping, specs, scratch, row_no, line, cells, places, year)
        keyed = [values.get(p, mapping.constants.get(p)) for p in mapping.key]
        if mapping.key and all(v in (None, '') for v in keyed):
            if current is None:
                doc = _new_doc(imp, len(docs) + 1, row_no, line)
                docs.append(doc)
                scratch.problems.append({'column': None, 'code': 'no_key', 'value': '', 'row': row_no,
                                         'key_column': column_of.get(mapping.key[-1]),
                                         'message': 'The row has no value in its key column'
                                                    + ('s' if len(mapping.key) > 1 else '') + '.'})
            else:
                doc = current  # the document above goes on: its number is written on its first row only
        elif mapping.key:
            hashed = imp.key_hash(source, kind.name, keyed)
            doc = by_key.get(hashed)
            if doc is None:
                doc = by_key[hashed] = _new_doc(imp, len(docs) + 1, row_no, line)
                doc.key_hash = hashed
                docs.append(doc)
        else:
            content = json.dumps(values, sort_keys=True, default=str)
            occurrences[content] = occurrences.get(content, 0) + 1
            doc = _new_doc(imp, len(docs) + 1, row_no, line)
            doc.key_hash = imp.key_hash(source, kind.name, [content, occurrences[content]])
            docs.append(doc)
        _absorb(doc, scratch, values, row_no, line, cells)
        for path, value in values.items():
            array = kinds.line_array(path)
            if array is not None:
                continue
            seen = doc.header.get(path)
            if seen is None:
                doc.header[path] = (value, row_no)
            elif imp.norm_key(seen[0]) != imp.norm_key(value):
                column = column_of.get(path)
                raw = cells[position[path]] if position.get(path, len(cells)) < len(cells) else ''
                doc.problems.append({'column': column, 'code': 'header_conflict', 'value': raw, 'row': row_no,
                                     'message': f'one {kind.name.replace("-", " ")} has one {path}: row '
                                                f'{seen[1]} gives {seen[0]!r}, this row {value!r}'})
        for array in kind.lines:
            leaves = {p.split('[].', 1)[1]: v for p, v in values.items() if p.startswith(array + '[].')}
            if leaves:
                doc.line_rows.setdefault(array, []).append(row_no)
                doc.line_values.setdefault(array, []).append(leaves)
        current = doc
    for doc in docs:
        _finish(imp, kind, doc, mapping, column_of)
    return docs


def _new_doc(imp, number: int, row_no: int, line: int):
    doc = imp.RowPlan(number, line, [])
    doc.header, doc.line_values = {}, {}
    return doc


def _absorb(doc, scratch, values, row_no, line, cells) -> None:
    doc.spans.append([row_no, line])
    doc.cells.append(cells)
    doc.problems.extend(scratch.problems)
    doc.dropped.extend(scratch.dropped)
    doc.originals.update(scratch.originals)


def _finish(imp, kind: kinds.DocKind, doc, mapping, column_of: dict) -> None:
    payload: dict = {}
    for path, (value, _) in doc.header.items():
        imp._set_path(payload, path, value)
    for path, value in mapping.constants.items():
        if kinds.line_array(path) is None and imp._get_path(payload, path) is None:
            imp._set_path(payload, path, value)
    for array in kind.lines:
        lines = []
        for leaves in doc.line_values.get(array, []):
            line: dict = {}
            for leaf, value in leaves.items():
                imp._set_path(line, leaf, value)
            for path, value in mapping.constants.items():
                if path.startswith(array + '[].') and imp._get_path(line, path.split('[].', 1)[1]) is None:
                    imp._set_path(line, path.split('[].', 1)[1], value)
            lines.append(line)
        if lines:
            payload[array] = lines
    del doc.header, doc.line_values
    first = doc.spans[0][0] if doc.spans else doc.row_no
    failed = {p['column'] for p in doc.problems}
    for name in kinds.required(kind.name):
        if name in kind.lines:
            if not payload.get(name) and not any(p['code'] == 'row_width' for p in doc.problems):
                doc.problems.append({'column': None, 'code': 'no_lines', 'value': '', 'row': first, 'required': True,
                                     'message': f'no {name.rstrip("s")} line: every row of the document leaves the '
                                                f'{name} columns blank, and `{kind.create}` needs one'})
        elif name in column_of and imp._get_path(payload, name) is None and column_of[name] not in failed:
            doc.problems.append({'column': column_of[name], 'code': 'E_VALIDATION', 'value': '', 'required': True,
                                 'row': first, 'message': f'blank, and `{kind.create}` requires {name}'})
    doc.payload = payload
    imp._settle(doc)


# ------------------------------------------------------------------ the books as documents meet them

class DocBooks(_imports().Books):
    """Lookups for a document file: the lists its references name (by name, number or code, held in memory), the
    numbers the books already use, and the key ledger. A document is never matched by name: no own index."""

    def __init__(self, s, kind: kinds.DocKind, source: str):
        self.s, self.kind, self.source = s, kind, source
        self._readable, self.refs, self.numbers = {}, {}, {}
        self.definition, self.table = kind.definition, None
        self.own = {'by_key': {}, 'ids': set(), 'order': [], 'inactive': set(), 'names': [], 'field': 'number',
                    'table': None, 'definition': None}
        self.ledger = {row[0]: (row[1], row[2]) for row in s.company.conn.execute(
            sa.select(c.import_keys.c.key_hash, c.import_keys.c.record_type, c.import_keys.c.record_id).where(
                c.import_keys.c.source == source, c.import_keys.c.released_by.is_(None)))}
        self.taken = _taken_numbers(s, kind)

    def selector(self, payload: dict) -> str | None:
        return None

    def existing(self, payload: dict) -> str | None:
        return None

    def made(self, payload: dict, record_id: str) -> None:
        if isinstance(payload, dict) and payload.get('number'):
            self.taken[_imports().norm_key(payload['number'])] = 'posted'


def _taken_numbers(s, kind: kinds.DocKind) -> dict[str, str]:
    """The numbers the books already use in this kind's series (`E_DUPLICATE_NUMBER` when typed), normalized, with
    the status of the document holding each: a deleted document keeps its number in its history."""
    if 'number' not in kinds.fields(kind.name):
        return {}
    table = c.metadata.tables[kind.table]
    family = NUMBER_FAMILIES.get(kind.family, (kind.family,))
    status = table.c.status if 'status' in table.c else sa.literal('')
    imp = _imports()
    rows = s.company.conn.execute(sa.select(table.c.number, status, table.c.id).where(
        table.c[kind.type_column].in_(family), table.c.number.is_not(None))).all()
    gone = deleted_ids(s, [r[2] for r in rows])
    return {imp.norm_key(n): 'deleted' if i in gone else (st or '') for n, st, i in rows}


DELETIONS = ('sales_deletions', 'payment_deletions', 'bill_deletions', 'credit_deletions', 'journal_deletions',
             'purchase_deletions', 'deposit_deletions')


def deleted_ids(s, ids: list[str]) -> set[str]:
    """Which of these documents were deleted (their tombstones; the transaction row keeps its history)."""
    if not ids:
        return set()
    found: set[str] = set()
    names = set(c.metadata.tables)
    for table in DELETIONS:
        if table in names and 'transaction_id' in c.metadata.tables[table].c:
            found |= {r[0] for r in s.company.raw.execute(
                f'SELECT transaction_id FROM {table} WHERE transaction_id IN (SELECT value FROM json_each(?))',
                (json.dumps(ids),)).fetchall()}
    return found


def find_document(s, target: str, number: str) -> dict | None:
    """An invoice or bill by its number (or id): id, version, status and, for an invoice, its customer."""
    t = c.transactions
    q = sa.select(t.c.id, t.c.version, t.c.status, t.c.number).where(
        t.c.type == target, sa.or_(t.c.number == number, t.c.id == number))
    if target == 'invoice':
        p = c.metadata.tables['sales_profiles']
        q = sa.select(t.c.id, t.c.version, t.c.status, t.c.number, p.c.customer_id).select_from(
            t.outerjoin(p, p.c.revision_id == t.c.current_revision_id)).where(
            t.c.type == target, sa.or_(t.c.number == number, t.c.id == number))
    rows = [dict(r) for r in s.company.conn.execute(q).mappings().all()]
    gone = deleted_ids(s, [r['id'] for r in rows])
    for r in rows:
        if r['id'] in gone:
            r['status'] = 'deleted'
    live = [r for r in rows if r['status'] not in ('deleted', 'voided')]
    return (live or rows or [None])[0]


def ref_items(kind: kinds.DocKind, payload: dict, line_rows: dict | None = None):
    """(path, target, value, row, setter) for each list reference the document holds: header fields, then each
    line's, with the file row the line came from. Invoices and bills found by number are not list references."""
    specs = kinds.fields(kind.name)
    out = []
    for path, spec in specs.items():
        if spec.type != 'reference' or spec.target in DOCUMENT_TARGETS:
            continue
        array = kinds.line_array(path)
        if array is None:
            if '.' in path:
                continue
            value = payload.get(path)
            if isinstance(value, str) and value:
                out.append((path, spec.target, value, None, _setter(payload, path)))
            continue
        leaf = path.split('[].', 1)[1]
        for i, line in enumerate(payload.get(array) or []):
            value = line.get(leaf)
            if isinstance(value, str) and value:
                rows = (line_rows or {}).get(array) or []
                out.append((path, spec.target, value, rows[i] if i < len(rows) else None, _setter(line, leaf)))
    return out


def _setter(node: dict, key: str):
    def set_(value):
        node[key] = value
    return set_


def resolve_payload(books, kind, payload: dict, columns: dict, decided: dict | None = None, creator=None,
                    line_rows: dict | None = None):
    """(input with list references as ids, every unknown reference, False, the skip decision that leaves it out)."""
    imp = _imports()
    out = json.loads(json.dumps(payload))
    unknown: list[dict] = []
    for path, target, value, row, set_ in ref_items(kind, out, line_rows):
        decision = (decided or {}).get((path, imp.norm_key(value)))
        if decision is not None and decision['action'] == 'skip':
            return None, [], False, decision
        wanted = decision['target'] if decision is not None and decision['action'] == 'map' else value
        found = books.resolve(target, wanted)
        if found is None and decision is not None and decision['action'] == 'create' and creator is not None:
            found = creator(target, path, value, decision)
        if found is None:
            unknown.append({'field': path, 'column': columns.get(path), 'target': target, 'value': value,
                            'decision': decision, 'row': row})
            continue
        set_(found)
    if unknown:
        return None, unknown, False, None
    return out, [], False, None


# ------------------------------------------------------------------ the check's half that needs the books

def check_documents(s, kind: kinds.DocKind, table: tabular.Table, mapping, docs: list, books) -> None:
    """What the command would refuse, found without running it: each document's input through the command's own
    input model; a reference to an inactive record; a number the books already use; a journal that does not
    balance; a payment's invoices (found by number, the payment's customer's, open) and amounts that exceed it."""
    from bookflow.core import registry
    from bookflow.core.dispatch import validate_input
    imp = _imports()
    command = registry.get(kind.create)
    columns = {p: table.headers[i] for i, p in mapping.columns.items()}
    position = {p: i for i, p in mapping.columns.items()}
    places = imp._places(s)
    for doc in docs:
        payload = doc.payload
        if payload is None or doc.key_hash in books.ledger:
            continue
        first = doc.spans[0][0]

        def problem(path, code, message, row=first, model=False, line_index=None):
            array = kinds.line_array(path) if path else None
            if array is not None and line_index is not None:
                rows = doc.line_rows.get(array) or []
                row = rows[line_index] if line_index < len(rows) else row
            column = columns.get(path) if path else None
            cells = doc.cells[[r for r, _ in doc.spans].index(row)] if row in [r for r, _ in doc.spans] else []
            cell = cells[position[path]] if path in position and position[path] < len(cells) else ''
            doc.problems.append({'column': column, 'code': code, 'value': cell, 'row': row, 'model': model,
                                 'message': message})
        for path, target, value, row, _ in ref_items(kind, payload, doc.line_rows):
            found = books.resolve(target, value)
            if found is not None and found in books.ref_index(target)['inactive']:
                doc.problems.append({'column': columns.get(path), 'code': 'E_INACTIVE_REFERENCE', 'value': value,
                                     'row': row or first,
                                     'message': f"{target.replace('-', ' ')} {value!r} is inactive; a document must "
                                                'name an active record'})
        number = payload.get('number')
        if isinstance(number, str) and imp.norm_key(number) in books.taken:
            held = books.taken[imp.norm_key(number)]
            problem('number', 'E_DUPLICATE_NUMBER', f'{kind.name.replace("-", " ")} {number} is already in the books'
                    + (' (a deleted document keeps its number in its history; give this one another number)'
                       if held == 'deleted' else '; a document takes a free number'))
        if kind.name == 'journal':
            sides = {'debit': 0, 'credit': 0}
            for line in payload.get('lines') or []:
                if line.get('side') in sides and isinstance(line.get('amount'), str):
                    sides[line['side']] += tabular.parse_money(line['amount'], places) or 0
            if sides['debit'] != sides['credit']:
                problem(None, 'unbalanced', f"the entry does not balance: debits {tabular.money_text(sides['debit'], places)}, "
                                            f"credits {tabular.money_text(sides['credit'], places)}")
        if kind.name in ('payment', 'bill-payment'):
            _check_applications(s, kind, payload, books, problem, places)
        body = command_body(kind, payload, placeholder=True)
        try:
            validate_input(command, body)
        except BookflowError as error:
            for f in [f for f in (error.details or {}).get('fields', []) if isinstance(f, dict)] or [{}]:
                path, index = _input_path(f.get('field'))
                text = f.get('problem') or f.get('message') or error.message
                if path is None and f.get('field') not in (None, 'input'):
                    text = f"{f['field']}: {text}"
                problem(path, error.code, text, model=True, line_index=index)
        imp._settle(doc)


def _check_applications(s, kind, payload: dict, books, problem, places: int) -> None:
    array, leaf = ('applications', 'invoice') if kind.name == 'payment' else ('bills', 'bill')
    lines = payload.get(array) or []
    customer = books.resolve('customer', payload['customer']) if isinstance(payload.get('customer'), str) else None
    total = 0
    for i, line in enumerate(lines):
        number = line.get(leaf)
        if not isinstance(number, str):
            continue
        found = find_document(s, leaf, number)
        path = f'{array}[].{leaf}'
        if found is None or found['status'] in ('deleted', 'voided'):
            problem(path, 'unknown_reference', f'no {leaf} {number!r} in the books', line_index=i)
        elif leaf == 'invoice' and customer is not None and found.get('customer_id') not in (None, customer):
            problem(path, 'E_VALIDATION', f"invoice {number} is another customer's, not {payload['customer']!r}",
                    line_index=i)
        if isinstance(line.get('amount'), str):
            total += tabular.parse_money(line['amount'], places) or 0
    if kind.name == 'payment':
        if len(lines) > 1 and any(not isinstance(line.get('amount'), str) for line in lines):
            problem(f'{array}[].amount', 'E_VALIDATION', 'a payment applied to more than one invoice needs the '
                                                         'amount applied to each')
        amount = tabular.parse_money(payload['amount'], places) if isinstance(payload.get('amount'), str) else None
        if amount is not None and total > amount:
            problem(f'{array}[].amount', 'E_VALIDATION', f'the amounts applied ({tabular.money_text(total, places)}) '
                                                         f'exceed the payment ({payload["amount"]})')


def _input_path(field: str | None) -> tuple[str | None, int | None]:
    """A model error's location (`lines.2.item`, `applications.items.0.amount`) as a mapped path and line index."""
    if not isinstance(field, str):
        return None, None
    parts = field.split('.')
    if len(parts) >= 3 and parts[1] == 'items' and parts[0] == 'applications' and parts[2].isdigit():
        return 'applications[].' + '.'.join(parts[3:]), int(parts[2])
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[0] + '[].' + '.'.join(parts[2:]), int(parts[1])
    return field, None


# ------------------------------------------------------------------ into the command's input

def command_body(kind: kinds.DocKind, payload: dict, *, placeholder: bool = False, s=None, run_id: str = '',
                 row_no: int = 0) -> dict:
    """The document as its command takes it: a payment's applications inline (by invoice, with its current version,
    the whole payment when it applies to one invoice and names no amount) and its operation key; a bill payment's
    bills. ``placeholder`` gives versions and keys any valid value, for the check's model validation."""
    body = json.loads(json.dumps(payload))
    if kind.name == 'payment':
        body['operation_key'] = 'import-check' if placeholder else f'import-{run_id}-{row_no}'
        lines = body.pop('applications', None) or []
        items = []
        for line in lines:
            invoice = line.get('invoice')
            version = 1
            if not placeholder and s is not None and isinstance(invoice, str):
                found = find_document(s, 'invoice', invoice)
                if found is not None:
                    invoice, version = found['id'], found['version']
            amount = line.get('amount') if line.get('amount') is not None else (
                body.get('amount') if len(lines) == 1 else None)
            item = {'invoice': invoice, 'expected_version': version}
            if amount is not None:
                item['amount'] = amount
            items.append(item)
        if items:
            body['applications'] = {'mode': 'inline', 'items': items}
    elif kind.name == 'bill-payment' and not placeholder and s is not None:
        for line in body.get('bills') or []:
            found = find_document(s, 'bill', line['bill']) if isinstance(line.get('bill'), str) else None
            if found is not None:
                line['bill'] = found['id']
    return body


def classify(s, ctx, run: dict, kind: kinds.DocKind, books, plan, columns: dict, *, dry_run: bool,
             decided: dict | None = None, creator=None):
    """Set a document's status: already in (the key ledger), skipped by a decision, waiting on or refused for an
    unknown reference, or create. Returns (action, the command's input, None)."""
    imp = _imports()
    if plan.status == 'refused' and (plan.payload is None or plan.code in imp.READER_CODES):
        return 'none', None, None
    if plan.key_hash in books.ledger:
        plan.status = 'already_in'
        plan.record_type, plan.record_id = books.ledger[plan.key_hash]
        plan.message = 'An earlier run of this source made it.'
        return 'none', None, None
    resolved, unknowns, _, skip = resolve_payload(books, kind, plan.payload or {}, columns, decided, creator,
                                                  plan.line_rows)
    if skip is not None:
        plan.status, plan.code = 'skipped', 'skipped_by_decision'
        plan.message = f"Skipped: {skip['value']!r} ({skip['field']}) was decided skip."
        return 'none', None, None
    if unknowns:
        first = unknowns[0]
        plan.status = 'waiting' if dry_run else 'refused'
        plan.code, plan.column = 'unknown_reference', first['column']
        plan.message = imp.unknown_message(first, books.suggestions(first['target'], first['value']))
        plan.unknown = unknowns
        return 'none', None, None
    plan.status = 'create' if dry_run else 'created'
    return 'create', command_body(kind, resolved, s=s, run_id=run['id'], row_no=plan.row_no), None


def error_rows(cells: Any, spans: list | None) -> list[tuple[list[str], int | None]]:
    """A refused document's own rows for the error file: each row's cells and file line."""
    if spans and cells and isinstance(cells[0], list):
        return [(row, line) for row, (_, line) in zip(cells, spans)]
    return [(cells or [], None)]
