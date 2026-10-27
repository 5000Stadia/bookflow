"""List imports: preview, run in slices, read back, reverse (R107 increment 1, design/bulk-import.md §2-§5, §8a).

An import is two calls on one stored run, so what ran is what was previewed. ``import preview`` reads the file
(``tabular.py``), maps each row to its kind's create command input (guessed from headers, overridable), keys it, and runs
it through the command in dry-run (validation, permission, references) without writing the books; it stores the run and
each row's outcome, mapped input and own cells. ``import run`` runs a bounded slice: each row is classified again
(rows revalidate when they run) and written through the ordinary command nested in the session, with its audit event,
its key in ``import_keys`` and its effect in the run's batch all in the command's own transaction. A run advances only
when someone calls it; a killed slice resumes at the first row without a run outcome and repeats nothing.

The run's writes are one **batch** (kind ``import``, ordering ``record``), reversed through ``batches.py``; a reversal
releases the run's keys, so the corrected file imports again as new.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, import_kinds as kinds, schema as c, tabular
from bookflow.core.ids import new_id

OWNER = batches.OWNER
REFUSED_SHOWN = 20
SAMPLE = 5
WRITES = ('created', 'updated')
UNIT_PRICES = frozenset(('price', 'cost'))
# Refusals the file itself decides, which no change to the books can clear: kept as they are when the row runs.
READER_CODES = frozenset(('row_width', 'bad_value', 'no_key', 'duplicate_in_file', 'E_AMOUNT_PRECISION', 'ambiguous_date'))


# ------------------------------------------------------------------ the batch adapter

def _release_keys(s, effect: dict, reversal_id: str) -> None:
    s.company.conn.execute(sa.update(c.import_keys).where(
        c.import_keys.c.record_type == effect['record_type'], c.import_keys.c.record_id == effect['record_id'],
        c.import_keys.c.batch_id == effect['batch_id'], c.import_keys.c.released_by.is_(None)).values(
        released_by=reversal_id, released_at=batches.now()))


batches.register(batches.Adapter('import', 'record', on_reversed=_release_keys, label='Import run'))


# ------------------------------------------------------------------ reading and mapping a file

def _attachment_bytes(s, selector: str) -> tuple[dict, bytes]:
    from bookflow.company import attachment_store as store
    row = s.company.conn.execute(sa.select(c.attachments).where(c.attachments.c.id == selector)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'attachment', 'selector': selector,
                                                           'field': 'attachment'})
    if row['collected_at'] is not None:
        raise BookflowError('E_IO', 'Attachment body has been collected.', {'check': 'collected_body'})
    folder = s.company.path.parent / 'attachments'
    with store.open_verified(folder, store.BodyInfo(row['sha256'], row['size_bytes'])) as body:
        return dict(row), body.read()


def norm_key(value: Any) -> str:
    text = unicodedata.normalize('NFC', '' if value is None else str(value))
    return ' '.join(text.casefold().split())


@dataclass
class Mapping:
    columns: dict[int, str]  # header position -> input path
    found_by: dict[int, str]  # header position -> given, synonym, exact
    unmapped: list[str]
    key: list[str]
    keyed_by_content: bool
    decimal: str
    thousands: str
    date_order: str | None
    constants: dict[str, Any]
    decisions: list[dict]

    def as_dict(self, headers: list[str]) -> dict:
        return {'columns': {headers[i]: p for i, p in self.columns.items()},
                'found_by': {headers[i]: how for i, how in self.found_by.items()},
                'unmapped': self.unmapped, 'key': self.key, 'keyed_by_content': self.keyed_by_content,
                'formats': {'decimal': self.decimal, 'thousands': self.thousands, 'date_order': self.date_order},
                'constants': self.constants}


def build_mapping(kind: kinds.Kind, table: tabular.Table, inp) -> Mapping:
    specs = kinds.fields(kind.name)
    guessed = kinds.guess(kind.name, table.headers)
    columns: dict[int, str] = {}
    found_by: dict[int, str] = {}
    for i, g in enumerate(guessed):
        if g['path']:
            columns[i], found_by[i] = g['path'], g['confidence']
    given = dict(inp.columns or {})
    by_header = {tabular.normalize_header(h): i for i, h in enumerate(table.headers)}
    for header, path in given.items():
        i = by_header.get(tabular.normalize_header(header))
        if i is None:
            raise BookflowError('E_VALIDATION', message=f'The file has no column {header!r}.', details={
                'field': 'columns', 'header': header, 'headers': table.headers})
        if path is None:
            columns.pop(i, None)
            found_by.pop(i, None)
            continue
        if not kinds.path_ok(kind.name, path):
            raise BookflowError('E_VALIDATION', message=(
                f'{path!r} is not a field of `{kind.create}`; `import template {kind.name}` lists them.'),
                details={'field': 'columns', 'header': header, 'path': path})
        for other, p in list(columns.items()):
            if p == path and other != i:
                columns.pop(other)
                found_by.pop(other, None)
        columns[i], found_by[i] = path, 'given'
    for path in (inp.constants or {}):
        if not kinds.path_ok(kind.name, path):
            raise BookflowError('E_VALIDATION', message=f'{path!r} is not a field of `{kind.create}`.',
                                details={'field': 'constants', 'path': path})
    mapped = set(columns.values()) | set(inp.constants or {})
    if inp.key:
        key = list(inp.key)
        for path in key:
            if path not in mapped:
                raise BookflowError('E_VALIDATION', message=f'Key {path!r} is not a mapped column.',
                                    details={'field': 'key', 'path': path})
    elif kind.name == 'account' and 'number' in mapped:
        key = ['number']
    elif 'name' in mapped:
        key = (['parent_id'] if 'parent_id' in mapped and kind.hierarchical else []) + ['name']
    else:
        key = []
    decisions = []
    money_cols = [i for i, p in columns.items() if specs.get(p) and specs[p].type == 'money']
    decimal, thousands = '.', ','
    if inp.decimal:
        decimal, thousands = inp.decimal, (',' if inp.decimal == '.' else '.')
    elif money_cols:
        found = tabular.number_format([cells[i] for _, cells in table.rows for i in money_cols if i < len(cells)])
        if found is None:
            decisions.append({'decision': 'decimal', 'columns': [table.headers[i] for i in money_cols],
                              'message': 'Amounts like 1.234 could be one thousand or one point two; pass decimal.'})
        else:
            decimal, thousands = found
    date_cols = [i for i, p in columns.items() if specs.get(p) and specs[p].type == 'date']
    date_order = inp.date_order
    if date_order is None and date_cols:
        try:
            date_order = tabular.date_order([cells[i] for _, cells in table.rows for i in date_cols if i < len(cells)])
        except tabular.CellError as error:
            date_order = None
            decisions.append({'decision': 'date_order', 'columns': [table.headers[i] for i in date_cols],
                              'message': error.message})
        else:
            if date_order is None:
                decisions.append({'decision': 'date_order', 'columns': [table.headers[i] for i in date_cols],
                                  'message': 'Every date could be day-first or month-first; pass date_order.'})
    unmapped = [h for i, h in enumerate(table.headers) if i not in columns]
    return Mapping(columns, found_by, unmapped, key, not key, decimal, thousands, date_order,
                   dict(inp.constants or {}), decisions)


def _set_path(payload: dict, path: str, value: Any) -> None:
    node = payload
    parts = path.split('.')
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _get_path(payload: dict, path: str) -> Any:
    node: Any = payload
    for part in path.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return _plain(node)


def _plain(value: Any) -> Any:
    """A shown money value (``{"amount": "12.50", "currency": ...}``) as the input takes it."""
    if isinstance(value, dict) and 'amount' in value and 'currency' in value:
        return value['amount']
    return value


def coerce(spec: kinds.FieldSpec | None, cell: str, mapping: Mapping, places: int, year: int) -> Any:
    """A cell as its field's declared type. References stay text here; they resolve when the row is checked."""
    if spec is None or spec.type in ('string', 'reference', 'any'):
        return cell
    if spec.type == 'money':
        if spec.path in UNIT_PRICES:
            from bookflow.core.unit_price import EXTRA_PLACES
            places += EXTRA_PLACES  # an item's price and cost carry sub-cent unit prices, as `item create` takes them
        return tabular.money_text(tabular.parse_money(cell, places, decimal=mapping.decimal,
                                                      thousands=mapping.thousands), places).rstrip('0').rstrip('.') \
            if spec.path in UNIT_PRICES else tabular.money_text(tabular.parse_money(
                cell, places, decimal=mapping.decimal, thousands=mapping.thousands), places)
    if spec.type == 'decimal':
        return tabular.parse_decimal(cell, decimal=mapping.decimal, thousands=mapping.thousands)
    if spec.type == 'date':
        return tabular.parse_date(cell, mapping.date_order, current_year=year)
    if spec.type == 'boolean':
        return tabular.parse_bool(cell)
    if spec.type == 'integer':
        text = tabular.parse_decimal(cell, decimal=mapping.decimal, thousands=mapping.thousands)
        if text is None or not text.lstrip('-').isdigit():
            raise tabular.CellError('bad_value', f'not a whole number: {cell!r}')
        return int(text)
    if spec.type == 'enum':
        wanted = norm_key(cell).replace('-', ' ').replace('_', ' ')
        for option in spec.enum:
            if option.replace('_', ' ') == wanted:
                return option
        raise tabular.CellError('bad_value', f'{cell!r} is not one of: {", ".join(spec.enum)}')
    return cell


@dataclass
class RowPlan:
    row_no: int
    line: int
    cells: list[str]
    payload: dict | None = None
    key_hash: str | None = None
    status: str | None = None
    code: str | None = None
    message: str | None = None
    column: str | None = None
    record_type: str | None = None
    record_id: str | None = None


def key_hash(source: str, kind: str, values: list[Any]) -> str:
    text = '|'.join([norm_key(source), kind] + [norm_key(v) for v in values])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plan_rows(kind: kinds.Kind, table: tabular.Table, mapping: Mapping, source: str, places: int,
              year: int) -> list[RowPlan]:
    specs = kinds.fields(kind.name)
    out: list[RowPlan] = []
    seen: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    for row_no, (line, cells) in enumerate(table.rows, start=1):
        plan = RowPlan(row_no, line, cells)
        out.append(plan)
        if row_no in table.width_errors:
            plan.status, plan.code = 'refused', 'row_width'
            plan.message = f'The row has {len(cells)} cells; the header has {len(table.headers)}.'
            continue
        payload: dict = {}
        try:
            for i, path in mapping.columns.items():
                cell = cells[i].strip() if i < len(cells) else ''
                if not cell:
                    continue  # a blank cell is omitted, never sent empty
                plan.column = table.headers[i]
                spec = specs.get(path)
                _set_path(payload, path, coerce(spec, cell, mapping, places, year))
            plan.column = None
        except tabular.CellError as error:
            plan.status, plan.code, plan.message = 'refused', error.code, f'{plan.column}: {error.message}'
            continue
        for path, value in mapping.constants.items():
            if _get_path(payload, path) is None:
                _set_path(payload, path, value)
        plan.payload = payload
        if mapping.key:
            values = [_get_path(payload, p) for p in mapping.key]
            if all(v in (None, '') for v in values):
                plan.status, plan.code = 'refused', 'no_key'
                plan.message = 'The row has no value in its key column' + ('s' if len(mapping.key) > 1 else '') + '.'
                continue
            plan.key_hash = key_hash(source, kind.name, values)
        else:
            content = json.dumps(payload, sort_keys=True)
            occurrences[content] = occurrences.get(content, 0) + 1
            plan.key_hash = key_hash(source, kind.name, [content, occurrences[content]])
        if plan.key_hash in seen and mapping.key:
            plan.status, plan.code = 'refused', 'duplicate_in_file'
            plan.message = f'Row {seen[plan.key_hash]} has the same key.'
            continue
        seen.setdefault(plan.key_hash, row_no)
    return out


# ------------------------------------------------------------------ the books as the rows meet them

class Books:
    """Lookups one call makes many times: the kind's own records by key, references by name, the key ledger."""

    def __init__(self, s, kind: kinds.Kind, source: str):
        self.s, self.kind, self.source = s, kind, source
        self.definition = kind.definition
        self.table = c.metadata.tables[self.definition.table]
        self.own = self._index(self.definition)
        self.numbers = {}
        if kind.name == 'account':
            self.numbers = {row[0]: row[1] for row in s.company.conn.execute(
                sa.select(self.table.c.number, self.table.c.id).where(self.table.c.number.is_not(None)))}
        self.refs: dict[str, dict] = {}
        self.ledger = {row[0]: (row[1], row[2]) for row in s.company.conn.execute(
            sa.select(c.import_keys.c.key_hash, c.import_keys.c.record_type, c.import_keys.c.record_id).where(
                c.import_keys.c.source == source, c.import_keys.c.released_by.is_(None)))}

    def _index(self, definition) -> dict:
        from bookflow.company.list_service import _key_expression, _selector_field
        table = c.metadata.tables[definition.table]
        field_ = _selector_field(definition)
        active = table.c.active if 'active' in table.c else sa.true()
        rows = self.s.company.conn.execute(sa.select(table.c.id, _key_expression(table, field_), table.c[field_],
                                                     active)).all()
        return {'by_key': {row[1]: row[0] for row in rows}, 'ids': {row[0] for row in rows},
                'names': [row[2] for row in rows], 'field': field_, 'table': table, 'definition': definition}

    def ref_index(self, noun: str) -> dict:
        if noun not in self.refs:
            from bookflow.company import lists
            definition = lists.get_list_definition(noun)
            self.refs[noun] = self.own if noun == self.kind.noun else self._index(definition)
        return self.refs[noun]

    def resolve(self, noun: str, value: str) -> str | None:
        from bookflow.core.ids import is_ulid, normalize_ulid
        index = self.ref_index(noun)
        text = unicodedata.normalize('NFC', value.strip())
        if is_ulid(text) and normalize_ulid(text) in index['ids']:
            return normalize_ulid(text)
        found = index['by_key'].get(unicodedata.normalize('NFC', text.casefold()))
        if found is None and noun == 'account':
            found = self.numbers.get(text) if self.kind.name == 'account' else self._account_number(text)
        if found is None and index['definition'].code_field:
            column = index['table'].c[index['definition'].code_field]
            found = self.s.company.conn.execute(sa.select(index['table'].c.id).where(column == text).limit(1)).scalar()
        return found

    def _account_number(self, text: str) -> str | None:
        table = c.metadata.tables['accounts']
        return self.s.company.conn.execute(sa.select(table.c.id).where(table.c.number == text)).scalar()

    def suggestions(self, noun: str, value: str) -> list[str]:
        names = self.ref_index(noun)['names']
        return difflib.get_close_matches(value, names, n=3, cutoff=0.6) or difflib.get_close_matches(
            value.casefold(), [n.casefold() for n in names], n=3, cutoff=0.6)

    def selector(self, payload: dict) -> str | None:
        """The text that names this row's record among the kind's own: full name, or an account's number."""
        if self.kind.name == 'account' and payload.get('number'):
            return str(payload['number'])
        name = payload.get('name')
        if not isinstance(name, str):
            return None
        parent = payload.get('parent_id') if self.kind.hierarchical else None
        if isinstance(parent, str) and parent:
            if parent in self.own['ids']:
                full = self.s.company.conn.execute(sa.select(self.table.c.full_name).where(
                    self.table.c.id == parent)).scalar() or parent
                return f'{full}:{name}'
            return f'{parent}:{name}'
        return name

    def existing(self, payload: dict) -> str | None:
        text = self.selector(payload)
        if text is None:
            return None
        if self.kind.name == 'account' and payload.get('number'):
            return self.numbers.get(text)
        return self.own['by_key'].get(unicodedata.normalize('NFC', text.strip().casefold()))

    def made(self, payload: dict, record_id: str) -> None:
        text = self.selector(payload)
        if text:
            self.own['by_key'][unicodedata.normalize('NFC', text.strip().casefold())] = record_id
            self.own['ids'].add(record_id)
            self.own['names'].append(text)
        if self.kind.name == 'account' and payload.get('number'):
            self.numbers[str(payload['number'])] = record_id


def _references(kind: kinds.Kind) -> dict[str, str]:
    return {path: spec.target for path, spec in kinds.fields(kind.name).items() if spec.type == 'reference'}


def resolve_payload(books: Books, kind: kinds.Kind, payload: dict, pending: set[str],
                    columns: dict[str, str]) -> tuple[dict | None, dict | None, bool]:
    """(input with references as ids, an unknown reference, whether a reference waits on an earlier row)."""
    out = json.loads(json.dumps(payload))
    waits = False
    for path, target in _references(kind).items():
        value = out.get(path)
        if not isinstance(value, str) or not value:
            continue
        found = books.resolve(target, value)
        if found is None and target == kind.noun and norm_key(value) in pending:
            waits = True
            continue
        if found is None:
            return None, {'field': path, 'column': columns.get(path), 'target': target, 'value': value,
                          'suggestions': books.suggestions(target, value)}, False
        out[path] = found
    if kind.name == 'sales-rep' and isinstance(out.get('name_id'), str) and out.get('name_type'):
        target = out['name_type'].replace('_', '-')
        found = books.resolve(target, out['name_id'])
        if found is None:
            return None, {'field': 'name_id', 'column': columns.get('name_id'), 'target': target,
                          'value': out['name_id'], 'suggestions': books.suggestions(target, out['name_id'])}, False
        out['name_id'] = found
    return out, None, waits


def _message(error: BookflowError) -> str:
    """The command's own message, with the fields it names when that message is only the generic one."""
    problems = [f"{f.get('field')}: {f.get('problem') or f.get('message') or f.get('type')}"
                for f in (error.details or {}).get('fields', []) if isinstance(f, dict)]
    if problems and error.message in ('Invalid input.',):
        return error.message[:-1] + ' (' + '; '.join(problems[:5]) + ').'
    return error.message


def _column_of(error: BookflowError, columns: dict[str, str]) -> str | None:
    details = error.details or {}
    candidates = [details.get('field')] + [f.get('field') for f in details.get('fields', []) if isinstance(f, dict)]
    for name in candidates:
        if isinstance(name, str):
            for path, header in columns.items():
                if name == path or name.endswith('.' + path) or path.startswith(name + '.'):
                    return header
    return None


def _same(kind: kinds.Kind, payload: dict, current: dict) -> bool:
    specs = kinds.fields(kind.name)
    for path in _leaves(payload):
        want, have = _get_path(payload, path), _get_path(current, path)
        spec = specs.get(path)
        if spec is not None and spec.type == 'money' and have is not None:
            try:
                if tabular.parse_money(str(want), 6) != tabular.parse_money(str(have), 6):
                    return False
                continue
            except tabular.CellError:
                return False
        if (want is None) != (have is None) or norm_key(want) != norm_key(have):
            return False
    return True


def _leaves(payload: dict, prefix: str = '') -> list[str]:
    out = []
    for key, value in payload.items():
        path = prefix + key
        if isinstance(value, dict):
            out += _leaves(value, path + '.')
        else:
            out.append(path)
    return out


def _input_shaped(s, kind: kinds.Kind, current: dict, payload: dict) -> dict:
    """The record's current values for the top-level fields this row sets, shaped as the update command's input."""
    from bookflow.core import registry
    model = registry.get(kind.update).input_model
    out = {}
    for key in payload:
        if key not in model.model_fields:
            continue
        value = _plain(current.get(key))
        if isinstance(value, dict):
            sub = _get_annotation_fields(model, key)
            value = {k: v for k, v in value.items() if sub is None or k in sub}
        out[key] = value
    return out


def _get_annotation_fields(model, key: str):
    import typing
    annotation = model.model_fields[key].annotation
    for candidate in typing.get_args(annotation) or (annotation,):
        if hasattr(candidate, 'model_fields'):
            return set(candidate.model_fields)
    return None


# ------------------------------------------------------------------ classifying one row (preview and run alike)

def classify(s, ctx, run: dict, kind: kinds.Kind, books: Books, plan: RowPlan, pending: set[str],
             columns: dict[str, str], *, dry_run: bool) -> tuple[str, dict | None, dict | None]:
    """Set the row's status; returns (action, resolved input, before) where action is create, update or none."""
    if plan.status == 'refused' and (plan.payload is None or plan.code in READER_CODES):
        return 'none', None, None
    payload = plan.payload or {}
    if plan.key_hash in books.ledger:
        plan.status = 'already_in'
        plan.record_type, plan.record_id = books.ledger[plan.key_hash]
        plan.message = 'An earlier run of this source made it.'
        return 'none', None, None
    existing = books.existing(payload)
    mode = run['mode']
    if existing is None and mode == 'update':
        plan.status, plan.code, plan.message = 'skipped', 'missing', 'No record matches; update mode makes none.'
        return 'none', None, None
    resolved, unknown, waits = resolve_payload(books, kind, payload, pending, columns)
    if unknown is not None:
        plan.status, plan.code, plan.column = 'refused', 'unknown_reference', unknown['column']
        hint = f" Did you mean {', '.join(repr(x) for x in unknown['suggestions'])}?" if unknown['suggestions'] else ''
        plan.message = (f"No {unknown['target'].replace('-', ' ')} matches {unknown['value']!r} "
                        f"({unknown['field']}).{hint}")
        plan.unknown = unknown  # type: ignore[attr-defined]
        return 'none', None, None
    if existing is not None:
        plan.record_type, plan.record_id = kind.definition.record_type, existing
        current = batches.run_nested(s, ctx, f'{kind.noun} show', {kind.noun: existing},
                                     source_ref=f"import:{run['id']}")
        if _same(kind, resolved, current):
            plan.status = 'unchanged'
            return 'none', None, None
        if mode == 'create':
            plan.status, plan.code = 'differs', 'differs'
            changed = [p for p in _leaves(resolved) if norm_key(_get_path(resolved, p))
                       != norm_key(_get_path(current, p))]
            plan.message = 'A record with this name exists and differs in ' + ', '.join(changed[:8]) + '; nothing applied.'
            return 'none', None, None
        body = {kind.noun: existing, 'expected_version': current['version'], **resolved}
        plan.status = 'update' if dry_run else 'updated'
        return 'update', body, _input_shaped(s, kind, current, resolved)
    plan.status = 'create' if dry_run else 'created'
    if waits:
        plan.message = 'Refers to a record an earlier row makes; checked when it runs.'
        return 'deferred', resolved, None
    return 'create', resolved, None


def _dry_run(s, ctx, run, kind, plan: RowPlan, action: str, body: dict, columns: dict[str, str]) -> None:
    name = kind.create if action == 'create' else kind.update
    try:
        batches.run_nested(s, ctx, name, body, source_ref=f"import:{run['id']}:{plan.row_no}", dry_run=True)
    except BookflowError as error:
        plan.status, plan.code, plan.message = 'refused', error.code, _message(error)
        plan.column = _column_of(error, columns)
        plan.details = error.details  # type: ignore[attr-defined]


# ------------------------------------------------------------------ preview

def _places(s) -> int:
    from bookflow.core.unit_price import minor_units_of
    currency = (s.company_row or {}).get('home_currency') or s.company.conn.execute(
        sa.select(c.company_info.c.home_currency)).scalar() or 'USD'
    return minor_units_of(currency)


def _company_year(s) -> int:
    from bookflow.company.memorized_schedule import today
    return int(today(s.company_tz)[:4])


def prepare_preview(s, ctx, inp) -> dict:
    kind = kinds.kind(inp.kind)
    if (inp.content is None) == (inp.attachment is None):
        raise BookflowError('E_VALIDATION', message='Give the file as `content` or `attachment`, one of them.',
                            details={'field': 'content'})
    attachment_name = None
    if inp.attachment is not None:
        row, raw = _attachment_bytes(s, inp.attachment)
        attachment_name = row['original_filename']
    else:
        raw = inp.content
    table = tabular.read(raw, delimiter=inp.delimiter, header_row=inp.header_row, skip_rows=inp.skip_rows)
    mapping = build_mapping(kind, table, inp)
    source = inp.source or f'{kind.name} import'
    places, year = _places(s), _company_year(s)
    rows = plan_rows(kind, table, mapping, source, places, year)
    run = {'id': new_id(), 'mode': inp.mode, 'kind': kind.name}
    columns = {p: table.headers[i] for i, p in mapping.columns.items()}
    books = Books(s, kind, source)
    pending: set[str] = set()
    for plan in rows:
        action, body, _ = classify(s, ctx, run, kind, books, plan, pending, columns, dry_run=True)
        if action in ('create', 'update'):
            _dry_run(s, ctx, run, kind, plan, action, body, columns)
        if plan.status == 'create' and plan.payload:
            text = books.selector(plan.payload)
            if text:
                pending.add(norm_key(text))
    notes = []
    if table.encoding_guessed:
        notes.append(f'encoding: {table.encoding} (guessed); save the file as UTF-8 if any name reads wrongly')
    if mapping.keyed_by_content:
        notes.append('keyed by content: a re-run is safe only for unchanged rows')
    for decision in mapping.decisions:
        notes.append(decision['message'])
    return {'kind': kind, 'table': table, 'mapping': mapping, 'rows': rows, 'run_id': run['id'], 'source': source,
            'attachment': inp.attachment, 'file_name': inp.file_name or attachment_name, 'notes': notes,
            'input': inp, 'columns': columns}


def apply_preview(s, ctx, prepared: dict) -> dict:
    """Store the run and its preview rows (import tables only, never the books) in the caller's transaction."""
    table, mapping, inp = prepared['table'], prepared['mapping'], prepared['input']
    at = batches.now()
    s.company.conn.execute(sa.insert(c.import_runs).values(
        id=prepared['run_id'], kind=prepared['kind'].name, mode=inp.mode, atomic=inp.atomic, state='previewed',
        source=prepared['source'], mapping=json.dumps({**mapping.as_dict(table.headers),
                                                       'headers': table.headers, 'notes': prepared['notes'],
                                                       'decisions': mapping.decisions}, sort_keys=True),
        attachment_id=prepared['attachment'], file_name=prepared['file_name'], file_sha256=table.sha256,
        encoding=table.encoding, delimiter=table.delimiter, row_count=len(prepared['rows']),
        stop_after_refusals=inp.stop_after_refusals, batch_id=None, reason=ctx.reason, version=1, created_at=at,
        created_by=s.actor.id, created_via=str(getattr(ctx.interface, 'value', ctx.interface))[:16], updated_at=at))
    values = [_row_values(prepared['run_id'], plan, 'preview', at, keep=True) for plan in prepared['rows']]
    for start in range(0, len(values), 500):
        s.company.conn.execute(sa.insert(c.import_rows), values[start:start + 500])
    return show(s, ctx, prepared['run_id'], prepared=prepared)


def _row_values(run_id: str, plan: RowPlan, phase: str, at: str, *, keep: bool = False) -> dict:
    return {'run_id': run_id, 'row_no': plan.row_no, 'phase': phase, 'line': plan.line, 'key_hash': plan.key_hash,
            'status': plan.status, 'code': plan.code, 'message': plan.message, 'column_name': plan.column,
            'record_type': plan.record_type, 'record_id': plan.record_id,
            'payload': json.dumps(plan.payload, sort_keys=True) if keep and plan.payload is not None else None,
            'cells': json.dumps(plan.cells) if keep else None, 'created_at': at}


# ------------------------------------------------------------------ run

def _run_row(s, run_id: str) -> dict:
    row = s.company.conn.execute(sa.select(c.import_runs).where(c.import_runs.c.id == run_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'import_run', 'selector': run_id})
    return dict(row)


def _update_run(s, run: dict, **values) -> None:
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            s.company.conn.execute(sa.update(c.import_runs).where(c.import_runs.c.id == run['id']).values(
                version=c.import_runs.c.version + 1, updated_at=batches.now(), **values))
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    run.update(values)


def _insert_rows(s, values: list[dict]) -> None:
    if not values:
        return
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            s.company.conn.execute(sa.insert(c.import_rows), values)
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


def _pending_rows(s, run_id: str, limit: int) -> list[dict]:
    p, r = c.import_rows.alias('p'), c.import_rows.alias('r')
    q = (sa.select(p).select_from(p.outerjoin(r, sa.and_(r.c.run_id == p.c.run_id, r.c.row_no == p.c.row_no,
                                                         r.c.phase == 'run')))
         .where(p.c.run_id == run_id, p.c.phase == 'preview', r.c.row_no.is_(None)).order_by(p.c.row_no).limit(limit))
    return [dict(row) for row in s.company.conn.execute(q).mappings().all()]


def _counts(s, run_id: str, phase: str) -> dict[str, int]:
    rows = s.company.conn.execute(sa.select(c.import_rows.c.status, sa.func.count()).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == phase).group_by(c.import_rows.c.status)).all()
    return {status: int(n) for status, n in rows}


def run_slice(s, ctx, inp, *, interrupt=None) -> dict:
    """Run the next slice of a previewed run. ``interrupt`` is a test hook called after each row."""
    run = _run_row(s, inp.run)
    kind = kinds.kind(run['kind'])
    if run['state'] in ('cancelled', 'rolled_back'):
        raise BookflowError('E_VALIDATION', message=f"Run {run['id']} is {run['state']}; preview the file again.",
                            details={'field': 'run', 'state': run['state']})
    if run['state'] == 'done':
        return show(s, ctx, run['id'])
    mapping = json.loads(run['mapping'])
    if mapping.get('decisions'):
        raise BookflowError('E_VALIDATION', message=('The preview needs decisions first: '
                                                     + '; '.join(d['message'] for d in mapping['decisions'])
                                                     + ' Preview again with them.'),
                            details={'field': 'run', 'decisions': mapping['decisions']})
    if run['atomic'] and run['state'] == 'previewed' and _counts(s, run['id'], 'preview').get('refused'):
        raise BookflowError('E_VALIDATION', message=('An atomic run starts only when its preview refused no rows; '
                                                     'correct the file and preview again.'),
                            details={'field': 'run', 'refused': _counts(s, run['id'], 'preview')['refused']})
    if run['batch_id'] is None:
        batch_id = batches.open_batch(s, ctx, kind='import', label=(
            f"Import of {run['file_name'] or kind.name + 's'} ({run['row_count']} rows)"), scope=f'import:{kind.name}',
            source_type='import_run', source_id=run['id'], reason=ctx.reason or run['reason'])
        _update_run(s, run, batch_id=batch_id, state='running')
    elif run['state'] != 'running':
        _update_run(s, run, state='running')
    columns = {p: h for h, p in mapping['columns'].items()}
    books = Books(s, kind, run['source'])
    deadline = time.monotonic() + inp.seconds
    refused_now = done_now = 0
    buffered: list[dict] = []
    stopped = False
    for stored in _pending_rows(s, run['id'], inp.limit):
        plan = RowPlan(stored['row_no'], stored['line'], json.loads(stored['cells'] or '[]'),
                       json.loads(stored['payload']) if stored['payload'] else None, stored['key_hash'])
        if stored['status'] == 'refused' and (stored['payload'] is None or stored['code'] in READER_CODES):
            plan.status, plan.code, plan.message, plan.column = 'refused', stored['code'], stored['message'], \
                stored['column_name']
            action, body, before = 'none', None, None
        else:
            action, body, before = classify(s, ctx, run, kind, books, plan, set(), columns, dry_run=False)
        if action in ('create', 'update', 'deferred'):
            command = kind.update if action == 'update' else kind.create
            plan.status = 'updated' if action == 'update' else 'created'
            if action == 'deferred':
                body, unknown, _ = resolve_payload(books, kind, plan.payload or {}, set(), columns)
                if unknown is not None:
                    plan.status, plan.code, plan.column = 'refused', 'unknown_reference', unknown['column']
                    plan.message = f"No {unknown['target']} matches {unknown['value']!r} ({unknown['field']})."
                    body = None
            if body is not None:
                _insert_rows(s, buffered)
                buffered = []
                try:
                    output = batches.run_nested(s, ctx, command, body,
                                                source_ref=f"import:{run['id']}:{plan.row_no}",
                                                recorder=_row_recorder(run, kind, plan, before))
                except BookflowError as error:
                    plan.status, plan.code, plan.message = 'refused', error.code, _message(error)
                    plan.column = _column_of(error, columns)
                    plan.record_type = plan.record_id = None
                else:
                    if action != 'update':
                        books.made(plan.payload or {}, output.get('id'))
                        books.ledger[plan.key_hash] = (kind.definition.record_type, output.get('id'))
        if plan.status not in WRITES:
            buffered.append(_row_values(run['id'], plan, 'run', batches.now()))
        done_now += 1
        if plan.status == 'refused':
            refused_now += 1
            if run['atomic']:
                _insert_rows(s, buffered)
                return _roll_back(s, ctx, run, plan)
            if refused_now >= run['stop_after_refusals']:
                stopped = True
        if interrupt is not None:
            interrupt(done_now)
        if stopped or time.monotonic() > deadline:
            break
    _insert_rows(s, buffered)
    remaining = len(_pending_rows(s, run['id'], 1))
    state = 'stopped' if stopped and remaining else ('done' if not remaining else 'running')
    if state != run['state']:
        _update_run(s, run, state=state)
    return show(s, ctx, run['id'], done_now=done_now)


def _row_recorder(run: dict, kind: kinds.Kind, plan: RowPlan, before: dict | None):
    record_type = kind.definition.record_type

    def record(s, cmd, applied, output):
        event_id = batches.last_event_id(s)
        found = batches.touched_effects(applied, {record_type})
        if not found and isinstance(output, dict) and output.get('id'):
            found = [(record_type, output['id'], 'created' if cmd.name == kind.create else 'updated',
                      output.get('version'))]
        main = output.get('id') if isinstance(output, dict) else None
        for rt, rid, action, version in found:
            if rid != main:
                continue
            batches.record_effect(s, run['batch_id'], record_type=rt, record_id=rid, action=action,
                                  version_after=version, before=before if action == 'updated' else None,
                                  event_id=event_id)
        if cmd.name == kind.create and main:
            s.company.conn.execute(sa.insert(c.import_keys).values(
                id=new_id(), source=run['source'], key_hash=plan.key_hash, record_type=record_type, record_id=main,
                run_id=run['id'], batch_id=run['batch_id'], created_at=batches.now()))
        plan.record_type, plan.record_id = record_type, main
        s.company.conn.execute(sa.insert(c.import_rows).values(_row_values(run['id'], plan, 'run', batches.now())))
    return record


def _roll_back(s, ctx, run: dict, plan: RowPlan) -> dict:
    """An atomic run met a refusal: undo what it made through its batch, and end it."""
    made = batches.counts(s, run['batch_id'])
    if made:
        batches.reverse(s, ctx, run['batch_id'], reason=f'Atomic import stopped at row {plan.row_no}: {plan.code}',
                        limit=10 ** 9)
    _update_run(s, run, state='rolled_back')
    out = show(s, ctx, run['id'])
    out['notes'].append(f'Row {plan.row_no} was refused ({plan.code}: {plan.message}); the atomic run undid the '
                        f'{sum(made.values())} record(s) it had made.')
    return out


def cancel(s, ctx, run_id: str) -> dict:
    run = _run_row(s, run_id)
    if run['state'] in ('done', 'cancelled', 'rolled_back'):
        raise BookflowError('E_VALIDATION', message=f"Run {run_id} is already {run['state']}.",
                            details={'field': 'run', 'state': run['state']})
    _update_run(s, run, state='cancelled')
    return show(s, ctx, run_id)


def reverse(s, ctx, inp, *, dry_run: bool) -> dict:
    run = _run_row(s, inp.run)
    if run['batch_id'] is None:
        raise BookflowError('E_VALIDATION', message=f'Run {inp.run} has made nothing to reverse.',
                            details={'field': 'run', 'state': run['state']})
    if not dry_run and run['state'] == 'running':
        _update_run(s, run, state='stopped')
    out = batches.reverse(s, ctx, run['batch_id'], reason=ctx.reason, dry_run=dry_run, limit=inp.limit,
                          fingerprint=inp.fingerprint)
    out['next'] = (f"import reverse {inp.run}" if out['state'] == 'reversing'
                   else (f"import reverse {inp.run} --fingerprint {out['fingerprint']}" if dry_run else None))
    return out


# ------------------------------------------------------------------ reading

def show(s, ctx, run_id: str, *, done_now: int = 0, prepared: dict | None = None) -> dict:
    run = _run_row(s, run_id)
    mapping = json.loads(run['mapping'])
    phase = 'run' if run['state'] != 'previewed' else 'preview'
    counts = _counts(s, run_id, phase)
    if phase == 'run':
        counts['not_run'] = run['row_count'] - sum(counts.values())
    refused = [_row_out(r) for r in s.company.conn.execute(sa.select(c.import_rows).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == phase, c.import_rows.c.status == 'refused')
        .order_by(c.import_rows.c.row_no).limit(REFUSED_SHOWN)).mappings()]
    unknown: dict[tuple, dict] = {}
    sample = []
    if prepared is not None:
        for plan in prepared['rows']:
            u = getattr(plan, 'unknown', None)
            if u is not None:
                entry = unknown.setdefault((u['field'], norm_key(u['value'])), {
                    'field': u['field'], 'column': u['column'], 'target': u['target'], 'value': u['value'],
                    'rows': [], 'suggestions': u['suggestions']})
                entry['rows'].append(plan.row_no)
            if plan.status in ('create', 'update') and len(sample) < SAMPLE:
                sample.append({'row': plan.row_no, 'command': prepared['kind'].create if plan.status == 'create'
                               else prepared['kind'].update, 'input': plan.payload})
    batch = None
    if run['batch_id']:
        b = s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == run['batch_id'])).scalar()
        batch = {'id': run['batch_id'], 'state': b}
    next_ = None
    if run['state'] in ('previewed', 'running', 'stopped'):
        next_ = f'import run {run_id}'
        if mapping.get('decisions'):
            next_ = 'import preview again with ' + ', '.join(d['decision'] for d in mapping['decisions'])
    elif run['state'] == 'done' and counts.get('refused'):
        next_ = f'import errors {run_id}'
    if prepared is not None and len(prepared['rows']) > 100:
        mapping['notes'] = list(mapping.get('notes', [])) + ['Over 100 rows: consider `company backup` first.']
    return {
        'run': {'id': run['id'], 'kind': run['kind'], 'mode': run['mode'], 'atomic': run['atomic'],
                'state': run['state'], 'source': run['source'], 'file_name': run['file_name'],
                'attachment': run['attachment_id'], 'file_sha256': run['file_sha256'], 'encoding': run['encoding'],
                'delimiter': tabular.DELIMITER_NAMES.get(run['delimiter'], run['delimiter']),
                'rows': run['row_count'], 'batch': batch, 'reason': run['reason'], 'created_at': run['created_at'],
                'created_by': run['created_by'], 'created_via': run['created_via'], 'updated_at': run['updated_at'],
                'version': run['version']},
        'counts': counts,
        'mapping': {k: mapping.get(k) for k in ('columns', 'found_by', 'unmapped', 'key', 'keyed_by_content',
                                                 'formats', 'constants')},
        'unknown_references': list(unknown.values()),
        'decisions': mapping.get('decisions', []),
        'refused': refused,
        'sample': sample,
        'notes': list(mapping.get('notes', [])),
        'done_now': done_now,
        'next': next_,
    }


def _row_out(r) -> dict:
    return {'row': r['row_no'], 'line': r['line'], 'status': r['status'], 'code': r['code'], 'message': r['message'],
            'column': r['column_name'], 'record_type': r['record_type'], 'record_id': r['record_id']}


def query(s, ctx, inp) -> dict:
    q = sa.select(c.import_runs).order_by(c.import_runs.c.created_at.desc(), c.import_runs.c.id.desc())
    if inp.kind:
        q = q.where(c.import_runs.c.kind == inp.kind)
    if inp.state:
        q = q.where(c.import_runs.c.state == inp.state)
    if inp.cursor:
        q = q.where(c.import_runs.c.id < inp.cursor)
    rows = [dict(r) for r in s.company.conn.execute(q.limit(inp.limit + 1)).mappings().all()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    items = []
    for r in rows:
        phase = 'run' if r['state'] != 'previewed' else 'preview'
        items.append({'id': r['id'], 'kind': r['kind'], 'mode': r['mode'], 'state': r['state'], 'source': r['source'],
                      'file_name': r['file_name'], 'rows': r['row_count'], 'batch': r['batch_id'],
                      'counts': _counts(s, r['id'], phase), 'created_at': r['created_at'],
                      'created_by': r['created_by'], 'reason': r['reason']})
    return {'items': items, 'next_cursor': rows[-1]['id'] if more and rows else None}


def rows(s, ctx, inp) -> dict:
    run = _run_row(s, inp.run)
    phase = inp.phase or ('run' if run['state'] != 'previewed' else 'preview')
    q = sa.select(c.import_rows).where(c.import_rows.c.run_id == inp.run, c.import_rows.c.phase == phase)
    if inp.status:
        q = q.where(c.import_rows.c.status == inp.status)
    if inp.cursor:
        q = q.where(c.import_rows.c.row_no > inp.cursor)
    found = [dict(r) for r in s.company.conn.execute(q.order_by(c.import_rows.c.row_no).limit(inp.limit + 1))
             .mappings().all()]
    more = len(found) > inp.limit
    found = found[:inp.limit]
    return {'run': inp.run, 'phase': phase, 'items': [_row_out(r) for r in found],
            'next_cursor': found[-1]['row_no'] if more and found else None}


def errors(s, ctx, run_id: str) -> dict:
    run = _run_row(s, run_id)
    mapping = json.loads(run['mapping'])
    phase = 'run' if run['state'] != 'previewed' else 'preview'
    refused = s.company.conn.execute(sa.select(c.import_rows.c.row_no, c.import_rows.c.code, c.import_rows.c.message,
                                               c.import_rows.c.line).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == phase, c.import_rows.c.status == 'refused')
        .order_by(c.import_rows.c.row_no)).all()
    cells = {r[0]: json.loads(r[1] or '[]') for r in s.company.conn.execute(sa.select(
        c.import_rows.c.row_no, c.import_rows.c.cells).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == 'preview',
        c.import_rows.c.row_no.in_([r[0] for r in refused])))} if refused else {}
    headers = list(mapping.get('headers') or [])
    body = []
    for row_no, code, message, line in refused:
        own = list(cells.get(row_no, []))
        own = own[:len(headers)] + [''] * (len(headers) - len(own))
        body.append(own + [code or '', message or '', str(line)])
    stem = (run['file_name'] or run['kind']).rsplit('.', 1)[0]
    return {'run': run_id, 'file_name': f'{stem} - refused rows.csv', 'rows': len(body),
            'content': tabular.write_csv(headers + ['code', 'message', 'line'], body)}


def kinds_list() -> dict:
    out = []
    for k in kinds.KINDS.values():
        specs = kinds.fields(k.name)
        required = [p for p, s in specs.items() if s.required]
        out.append({'kind': k.name, 'command': k.create, 'update_command': k.update, 'summary': k.summary,
                    'required': required, 'key': list(k.key_default),
                    'references': {p: s.target for p, s in specs.items() if s.type == 'reference'},
                    'example_header': template(k.name)['header']})
    return {'kinds': out}


_EXAMPLES = {'string': 'Example', 'money': '125.00', 'date': '2026-01-31', 'boolean': 'yes', 'integer': '30',
             'decimal': '2', 'reference': '', 'any': ''}


def template(kind_name: str) -> dict:
    k = kinds.kind(kind_name)
    specs = kinds.fields(kind_name)
    common = {'customer': ['name', 'company_name', 'email', 'phone', 'terms_id', 'billing_address.line1',
                           'billing_address.city', 'billing_address.state', 'billing_address.postal_code'],
              'vendor': ['name', 'company_name', 'email', 'phone', 'terms_id', 'address.line1', 'address.city',
                         'address.state', 'address.postal_code'],
              'item': ['name', 'type', 'description', 'price', 'cost', 'income_account_id', 'expense_account_id'],
              'account': ['number', 'name', 'type', 'description']}.get(k.name)
    paths = common or [p for p, s in specs.items() if s.required or p in ('name',)]
    paths = [p for p in dict.fromkeys([p for p, s in specs.items() if s.required] + paths) if p in specs]
    example = []
    for p in paths:
        spec = specs[p]
        if spec.type == 'enum':
            example.append(spec.enum[0])
        elif p == 'name':
            example.append({'customer': 'Acme Plumbing', 'vendor': 'Northside Supply', 'item': 'Service call',
                            'account': 'Office Supplies', 'term': 'Net 45'}.get(k.name, 'Example'))
        else:
            example.append(_EXAMPLES.get(spec.type, ''))
    return {'kind': k.name, 'command': k.create, 'header': paths, 'example': example,
            'content': tabular.write_csv(paths, [example]),
            'fields': [{'path': p, 'type': s.type, 'required': s.required, 'enum': list(s.enum) or None,
                        'reference_to': s.target} for p, s in specs.items()]}
