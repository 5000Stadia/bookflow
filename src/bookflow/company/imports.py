"""List imports: preview, run in slices, read back, reverse (R107 increment 1, design/bulk-import.md §2-§5, §8a).

An import is two calls on one stored run, so what ran is what was previewed. ``import preview`` reads the file
(``tabular.py``), maps each row to its kind's create command input (guessed from headers, overridable), keys it, and runs
it through the command in dry-run (validation, permission, references) without writing the books; it stores the run and
each row's outcome, mapped input and own cells. ``import run`` runs a bounded slice: each row is classified again
(rows revalidate when they run) and written through the ordinary command nested in the session, with its audit event,
its key in ``import_keys`` and its effect in the run's batch all in the command's own transaction. A run advances only
when someone calls it; a killed slice resumes at the first row without a run outcome and repeats nothing.

**Kabe's rules (2026-10-27).** Before any preview, the file is **checked** (`import check`): every cell against its
field (type, length, pattern, range), the mapping for completeness, keys for repeats, references against the books,
every problem returned at once by column. A preview **refuses the whole file** on any problem unless ``partial``; an
all-or-nothing run re-checks the references before it writes and undoes itself at a refusal. Text **drops only what it
cannot hold** (control characters), reporting each by position; junk in a number, date, choice or reference refuses.
A blank cell leaves its field out; only what the single command requires may not be blank. An unknown reference
**asks** (`import decide`, increment 2) and the run waits; saved mappings match next month's file (``import_mappings``).

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
DOC_UNIT_PRICES = frozenset(('unit_price', 'unit_cost', 'price_basis_amount', 'rate'))
INT64_MAX = 2 ** 63 - 1
PLACEHOLDER_ID = '0' * 26  # what a reference becomes when a row is checked against its command's input model
# Text fields that are identifiers, numbers or codes rather than words: a stray character refuses, never dropped.
IDENTIFIER_LEAVES = frozenset(('number', 'barcode', 'currency', 'email', 'cc_email', 'website', 'phone', 'alt_phone',
                               'fax', 'postal_code', 'initials', 'tax_line', 'emergency_contact_phone',
                               'emergency_contact_email'))
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
    batches.require_read(s, 'attachment text')  # reading a file through an import is reading it
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
    value_maps: dict[str, dict[str, str]] = field(default_factory=dict)  # path -> {normalized file text: replacement}
    saved: dict | None = None  # the saved mapping used: source, version, id, how it matched

    @property
    def decimal_open(self) -> bool:
        """The decimal mark waits on a decision: a cell either reading accepts is not a problem meanwhile."""
        return any(d['decision'] == 'decimal' for d in self.decisions)

    def as_dict(self, headers: list[str]) -> dict:
        return {'columns': {headers[i]: p for i, p in self.columns.items()},
                'found_by': {headers[i]: how for i, how in self.found_by.items()},
                'unmapped': self.unmapped, 'key': self.key, 'keyed_by_content': self.keyed_by_content,
                'formats': {'decimal': self.decimal, 'thousands': self.thousands, 'date_order': self.date_order},
                'constants': self.constants, 'value_maps': self.value_maps, 'saved': self.saved}


def build_mapping(kind: kinds.Kind, table: tabular.Table, inp, saved: dict | None = None) -> Mapping:
    """The guessed mapping, or a saved one (its columns only: a header it never mapped stays unmapped), under what the
    caller gives (`columns`, `constants`, `key`, formats)."""
    specs = kinds.fields(kind.name)
    columns: dict[int, str] = {}
    found_by: dict[int, str] = {}
    by_header = {tabular.normalize_header(h): i for i, h in enumerate(table.headers)}
    if saved is None:
        for i, g in enumerate(kinds.guess(kind.name, table.headers)):
            if g['path']:
                columns[i], found_by[i] = g['path'], g['confidence']
    else:
        for header, path in saved['columns'].items():
            i = by_header.get(tabular.normalize_header(header))
            if i is not None and kinds.path_ok(kind.name, path):
                columns[i], found_by[i] = path, 'saved'
    given = dict(inp.columns or {})
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
    constants = {**(saved['defaults'] if saved else {}), **(inp.constants or {})}
    for path in constants:
        if not kinds.path_ok(kind.name, path):
            raise BookflowError('E_VALIDATION', message=f'{path!r} is not a field of `{kind.create}`.',
                                details={'field': 'constants', 'path': path})
    mapped = set(columns.values()) | set(constants)
    formats = (saved or {}).get('formats') or {}
    if inp.key or (saved and saved['key'] and set(saved['key']) <= mapped):
        key = list(inp.key or saved['key'])
        for path in key:
            if path not in mapped:
                raise BookflowError('E_VALIDATION', message=f'Key {path!r} is not a mapped column.',
                                    details={'field': 'key', 'path': path})
    elif kinds.is_document(kind):
        # A document's group key: its kind's key fields that are mapped, when the ones it cannot go without are.
        key = [p for p in kind.key_default if p in mapped] if kind.key_default and set(kind.key_core) <= mapped \
            else []
    elif kind.name == 'account' and 'number' in mapped:
        key = ['number']
    elif 'name' in mapped:
        key = (['parent_id'] if 'parent_id' in mapped and kind.hierarchical else []) + ['name']
    else:
        key = []
    decisions = []
    money_cols = [i for i, p in columns.items() if specs.get(p) and specs[p].type == 'money']
    decimal, thousands = '.', ','
    if inp.decimal or formats.get('decimal'):
        decimal = inp.decimal or formats['decimal']
        thousands = ',' if decimal == '.' else '.'
    elif money_cols:
        found = tabular.number_format([cells[i] for _, cells in table.rows for i in money_cols if i < len(cells)])
        if found is None:
            decisions.append({'decision': 'decimal', 'columns': [table.headers[i] for i in money_cols],
                              'message': 'Amounts like 1.234 could be one thousand or one point two; pass decimal.'})
        else:
            decimal, thousands = found
    date_cols = [i for i, p in columns.items() if specs.get(p) and specs[p].type == 'date']
    date_order = inp.date_order or (formats.get('date_order') if date_cols else None)
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
    return Mapping(columns, found_by, unmapped, key, not key, decimal, thousands, date_order, constants, decisions,
                   {p: dict(m) for p, m in ((saved or {}).get('value_maps') or {}).items()},
                   {k: saved[k] for k in ('source', 'version', 'id', 'match')} if saved else None)


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


def _read_number(read, cell: str, mapping: Mapping, *places):
    try:
        return read(cell, *places, decimal=mapping.decimal, thousands=mapping.thousands)
    except tabular.CellError:
        if not mapping.decimal_open:
            raise
        decimal, thousands = (',', '.') if mapping.decimal == '.' else ('.', ',')
        return read(cell, *places, decimal=decimal, thousands=thousands)


def _unit_price(path: str) -> bool:
    """An item's price or cost, or a document line's unit price or cost: sub-cent unit prices, as typed."""
    return path in UNIT_PRICES or ('[].' in path and path.rsplit('.', 1)[-1] in DOC_UNIT_PRICES)


def coerce(spec: kinds.FieldSpec | None, cell: str, mapping: Mapping, places: int, year: int) -> Any:
    """A cell as its field's declared type. References stay text here; they resolve when the row is checked."""
    if spec is None or spec.type in ('string', 'reference', 'any'):
        return cell
    if spec.type == 'money':
        if _unit_price(spec.path):
            from bookflow.core.unit_price import EXTRA_PLACES
            places += EXTRA_PLACES  # an item's price and cost carry sub-cent unit prices, as `item create` takes them
        amount = _read_number(tabular.parse_money, cell, mapping, places)
        if amount is None:  # a currency code or symbol with no number
            raise tabular.CellError('bad_value', f'not an amount: {cell!r}')
        if abs(amount) > INT64_MAX:
            raise tabular.CellError('E_VALUE_RANGE', f'{cell!r} is beyond the largest amount the books hold')
        text = tabular.money_text(amount, places)
        return text.rstrip('0').rstrip('.') if _unit_price(spec.path) else text
    if spec.type == 'decimal':
        return _read_number(tabular.parse_decimal, cell, mapping)
    if spec.type == 'date':
        return tabular.parse_date(cell, mapping.date_order, current_year=year)
    if spec.type == 'boolean':
        return tabular.parse_bool(cell)
    if spec.type == 'integer':
        text = _read_number(tabular.parse_decimal, cell, mapping)
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
    problems: list[dict] = field(default_factory=list)  # every cell the check found wrong, in column order
    dropped: list[dict] = field(default_factory=list)  # characters a text field could not hold, dropped
    originals: dict[str, str] = field(default_factory=dict)  # path -> the file's text, where a value map changed it
    spans: list = field(default_factory=list)  # a document's file rows: [row number, line] each
    line_rows: dict = field(default_factory=dict)  # a document's line array -> the file row of each line


def key_hash(source: str, kind: str, values: list[Any]) -> str:
    text = '|'.join([norm_key(source), kind] + [norm_key(v) for v in values])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


_PATTERNS: dict[str, Any] = {}


def _fits(spec: kinds.FieldSpec, value: Any) -> None:
    """A value against its field's declared length, pattern and range, as the command's input model would hold it."""
    import re
    if isinstance(value, str) and spec.type in ('string', 'any') and not spec.path.endswith('_id'):
        # (an `_id` field resolved from a name, as a sales rep's `name_id`, is checked as the reference it is)
        if spec.max_length is not None and len(value) > spec.max_length:
            raise tabular.CellError('E_VALIDATION', f'{len(value)} characters; at most {spec.max_length} fit')
        if spec.min_length is not None and len(value) < spec.min_length:
            raise tabular.CellError('E_VALIDATION', f'at least {spec.min_length} characters are needed')
        if spec.pattern:
            compiled = _PATTERNS.get(spec.pattern) or _PATTERNS.setdefault(spec.pattern, re.compile(spec.pattern))
            if not compiled.search(value):
                raise tabular.CellError('E_VALIDATION', f'{value!r} is not in the form this field takes')
    if spec.type == 'integer' and isinstance(value, int):
        if (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
            bounds = ' to '.join(str(int(b)) for b in (spec.minimum, spec.maximum) if b is not None)
            raise tabular.CellError('E_VALIDATION', f'{value} is outside {bounds}')


def free_text(spec: kinds.FieldSpec | None, path: str, key: list[str] | set[str] = ()) -> bool:
    """Words, where a character no field holds is dropped (Kabe 2026-10-27), as against a number, date, choice,
    reference, code or identifier, where it refuses: an account number, an email, a phone, a currency, a mapped key
    other than the record's name. A name is words even as the key (Kabe's own example: "Johnson, Beck")."""
    if spec is None:
        return True  # a custom field's text
    if spec.type not in ('string', 'any') or path.endswith('_id'):
        return False
    leaf = path.rsplit('.', 1)[-1]
    if spec.pattern or leaf in IDENTIFIER_LEAVES or leaf.endswith(('_number', '_last4', '_date', '_code', '_ref')):
        return False
    return path not in key or leaf == 'name'


def _junk_controls(cell: str) -> None:
    """A number, date, choice, code or reference holding a control character (a tab or line break inside it too) is
    junk: refused, never repaired."""
    found = [(i, ch) for i, ch in enumerate(cell, start=1) if tabular.unholdable(ch, False)]
    if found:
        named = ', '.join(f'{tabular.char_name(ch)} at {i}' for i, ch in found[:3])
        raise tabular.CellError('bad_value', f'{cell!r} holds a control character ({named})')


def plan_rows(kind: kinds.Kind, table: tabular.Table, mapping: Mapping, source: str, places: int,
              year: int, mode: str = 'create') -> list[RowPlan]:
    """Each row read against its fields, every problem kept (Kabe 2026-10-27): text drops only the characters it
    cannot hold, and says which; a number, date, choice or reference that is not exactly one is junk and refuses the
    row; a blank cell leaves its field out; only what the command itself requires may not be blank."""
    specs = kinds.fields(kind.name)
    required = [p for p in kinds.required(kind.name, mode) if p not in mapping.key] if mode == 'create' else []
    column_of = {p: table.headers[i] for i, p in mapping.columns.items()}
    key_columns = {column_of.get(p) for p in mapping.key}
    out: list[RowPlan] = []
    seen: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    for row_no, (line, cells) in enumerate(table.rows, start=1):
        plan = RowPlan(row_no, line, cells)
        out.append(plan)
        if row_no in table.width_errors:
            plan.status, plan.code = 'refused', 'row_width'
            plan.message = f'The row has {len(cells)} cells; the header has {len(table.headers)}.'
            plan.problems.append({'column': None, 'code': 'row_width', 'value': '', 'message': plan.message})
            continue
        payload: dict = {}
        for path, value in read_cells(kind, table, mapping, specs, plan, row_no, line, cells, places, year).items():
            _set_path(payload, path, value)
        for path, value in mapping.constants.items():
            if _get_path(payload, path) is None:
                _set_path(payload, path, value)
        failed = {p['column'] for p in plan.problems}
        for name in required:
            if name in column_of and name not in payload and column_of[name] not in failed:
                plan.problems.append({'column': column_of[name], 'code': 'E_VALIDATION', 'value': '', 'required': True,
                                      'message': f'blank, and `{kind.create}` requires {name}'})
        plan.payload = payload
        if mapping.key:
            values = [_get_path(payload, p) for p in mapping.key]
            if all(v in (None, '') for v in values) and not key_columns & failed:
                message = 'The row has no value in its key column' + ('s' if len(mapping.key) > 1 else '') + '.'
                plan.problems.append({'column': None, 'code': 'no_key', 'value': '', 'message': message,
                                      'key_column': column_of.get(mapping.key[-1])})
            elif not all(v in (None, '') for v in values):
                plan.key_hash = key_hash(source, kind.name, values)
        else:
            content = json.dumps(payload, sort_keys=True)
            occurrences[content] = occurrences.get(content, 0) + 1
            plan.key_hash = key_hash(source, kind.name, [content, occurrences[content]])
        if plan.key_hash in seen and mapping.key:
            plan.problems.append({'column': None, 'code': 'duplicate_in_file', 'value': '',
                                  'message': f'Row {seen[plan.key_hash]} has the same key.',
                                  'key_column': column_of.get(mapping.key[-1])})
        elif plan.key_hash:
            seen.setdefault(plan.key_hash, row_no)
        _settle(plan)
    return out


def read_cells(kind, table: tabular.Table, mapping: Mapping, specs: dict, plan: RowPlan, row_no: int, line: int,
               cells: list[str], places: int, year: int) -> dict[str, Any]:
    """One row's mapped cells as their fields' values, by path: text drops what it cannot hold (each recorded in
    ``plan.dropped``), anything else not exactly its type is a problem in ``plan.problems``; blanks are left out."""
    values: dict[str, Any] = {}
    for i, path in mapping.columns.items():
        raw = cells[i] if i < len(cells) else ''
        header = table.headers[i]
        spec = specs.get(path)
        text_field = free_text(spec, path, mapping.key)
        if text_field and raw:
            multiline = spec.multiline if spec else False
            cleaned, dropped = tabular.clean_text(raw, multiline)
            for position, ch in dropped:
                entry = {'row': row_no, 'line': line, 'column': header, 'position': position,
                         'character': f'U+{ord(ch):04X}', 'name': tabular.char_name(ch)}
                if ch in tabular.KEPT_CONTROLS and not (ch == '\n' and raw[position - 2:position - 1] == '\r'):
                    entry['replaced_with'] = 'SPACE'  # a single-line field: the break becomes one space
                plan.dropped.append(entry)
            cell = cleaned.strip()
        else:
            cell = raw.strip()
        maps = mapping.value_maps.get(path)
        if cell and maps:
            mapped = maps.get(norm_key(cell), cell)
            if mapped != cell:
                plan.originals[path] = cell  # a decision remembered keys on the file's own text
            cell = mapped
        if not cell:
            continue  # a blank cell is omitted, never sent empty
        try:
            if not text_field:
                _junk_controls(cell)
            value = coerce(spec, cell, mapping, places, year)
            if spec is not None:
                _fits(spec, value)
        except tabular.CellError as error:
            plan.problems.append({'column': header, 'code': error.code, 'value': raw, 'message': error.message,
                                  'row': row_no})
            continue
        values[path] = value
    return values


def _settle(plan: RowPlan) -> None:
    """A row with any problem is refused, naming the first; what runs is only what the check passed."""
    if not plan.problems:
        return
    first = plan.problems[0]
    plan.status, plan.code, plan.column = 'refused', first['code'], first['column']
    more = f' (and {len(plan.problems) - 1} more)' if len(plan.problems) > 1 else ''
    plan.message = (f"{first['column']}: {first['message']}" if first['column'] else first['message']) + more
    if plan.payload is not None:
        plan.checked = plan.payload  # type: ignore[attr-defined]  (its references are still checked)
    plan.payload = None  # this row never runs


def check_constants(kind: kinds.Kind, mapping: Mapping, places: int, year: int) -> list[dict]:
    """Constants and a saved mapping's defaults go through the same reading as a cell: each becomes its field's
    type, or a problem of the whole mapping."""
    specs = kinds.fields(kind.name)
    problems = []
    for path, value in list(mapping.constants.items()):
        spec = specs.get(path)
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool) and spec is not None and \
                    spec.type in ('money', 'decimal'):
                value = str(value)
            if isinstance(value, str) and spec is not None:
                if not free_text(spec, path, mapping.key):
                    _junk_controls(value.strip())
                value = coerce(spec, value.strip(), mapping, places, year)
            if spec is not None and value is not None:
                _fits(spec, value)
        except tabular.CellError as error:
            problems.append({'column': None, 'code': error.code, 'value': str(value), 'rows': [],
                             'message': f'The constant {path}: {error.message}'})
            mapping.constants.pop(path)  # said once, for the file; no row repeats it
            continue
        mapping.constants[path] = value
    return problems


# ------------------------------------------------------------------ the books as the rows meet them

def documents():
    from bookflow.company import import_documents
    return import_documents


def books_for(s, kind, source: str):
    """The lookups a file of this kind needs: a list's own records and references, or a document file's."""
    return documents().DocBooks(s, kind, source) if kinds.is_document(kind) else Books(s, kind, source)


class Books:
    """Lookups one call makes many times: the kind's own records by key, references by name, the key ledger."""

    def __init__(self, s, kind: kinds.Kind, source: str):
        self.s, self.kind, self.source = s, kind, source
        self._readable: dict[str, bool] = {}
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
                'order': [row[0] for row in rows],
                'inactive': {row[0] for row in rows if not row[3]},
                'names': [row[2] for row in rows], 'field': field_, 'table': table, 'definition': definition}

    def readable(self, noun: str) -> bool:
        """Whether the caller may read that list: its names are offered as suggestions only then."""
        if noun not in self._readable:
            try:
                batches.require_read(self.s, f'{noun} show')
                self._readable[noun] = True
            except BookflowError:
                self._readable[noun] = False
        return self._readable[noun]

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
        """Up to three names to offer: look-alikes first (the test `cutover` flags duplicate parties with), then the
        closest by edit distance. Offered only; nothing is mapped on the importer's judgement. A list the caller may
        not read offers nothing."""
        if not self.readable(noun):
            return []
        names = self.ref_index(noun)['names']
        close = difflib.get_close_matches(value, names, n=3, cutoff=0.6) or difflib.get_close_matches(
            value.casefold(), [n.casefold() for n in names], n=3, cutoff=0.6)
        return list(dict.fromkeys(self.look_alikes(noun, value) + close))[:3]

    def look_alikes(self, noun: str, value: str) -> list[str]:
        from bookflow.company.cutover import alike_names
        if not self.readable(noun):
            return []
        return [n for n in self.ref_index(noun)['names'] if isinstance(n, str) and alike_names(value, n)][:3]

    def add_reference(self, noun: str, name: str, record_id: str) -> None:
        index = self.ref_index(noun)
        index['by_key'][unicodedata.normalize('NFC', name.strip().casefold())] = record_id
        index['ids'].add(record_id)
        index['inactive'].discard(record_id)
        index['names'].append(name)
        index['order'].append(record_id)

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


def _ref_paths(kind: kinds.Kind, payload: dict) -> list[tuple[str, str]]:
    """(path, list) of each reference the row's input can hold: the kind's declared ones, and a sales rep's name."""
    refs = list(_references(kind).items())
    if kind.name == 'sales-rep' and isinstance(payload.get('name_id'), str) and payload.get('name_type'):
        refs.append(('name_id', str(payload['name_type']).replace('_', '-')))
    return refs


def check_rows(s, kind: kinds.Kind, table: tabular.Table, mapping: Mapping, rows: list[RowPlan], books: 'Books',
               mode: str) -> None:
    """The check's half that needs the books, so it refuses what the preview and the run would: each row's input
    through its command's own input model (and the rules the command applies to the input alone), with references
    as a placeholder id, so model validators and requirements that depend on another field are met here; a reference
    to an inactive record; a row that is its own parent. A row that will update is checked as the update's input."""
    from bookflow.core import registry
    from bookflow.core.dispatch import validate_input
    columns = {p: table.headers[i] for i, p in mapping.columns.items()}
    position = {p: i for i, p in mapping.columns.items()}
    mapped = set(columns) | set(mapping.constants)
    unmapped = {n for n in kinds.required(kind.name, 'create') if not any(p == n or p.startswith(n + '.')
                                                                           for p in mapped)} if mode == 'create' else set()
    names = {}
    for plan in rows:
        payload = plan.payload if plan.payload is not None else getattr(plan, 'checked', None)
        text = books.selector(payload) if payload else None
        if text:
            names.setdefault(norm_key(text), plan.row_no)
    for plan in rows:
        payload = plan.payload
        if payload is None:
            continue
        existing = books.existing(payload) if mode != 'create' else None
        creating = mode == 'create' or (mode == 'upsert' and existing is None)
        if mode == 'create' and books.existing(payload) is not None:
            continue  # an existing record: `exists`, `unchanged` or `differs`, never this input
        probe = json.loads(json.dumps(payload))
        for path, target in _ref_paths(kind, payload):
            value = payload.get(path)
            if not isinstance(value, str) or not value:
                continue
            probe[path] = PLACEHOLDER_ID
            found = books.resolve(target, value)
            if found is None:
                if target == kind.noun and norm_key(value) == norm_key(payload.get('name')) and \
                        names.get(norm_key(value), plan.row_no) == plan.row_no:
                    plan.problems.append({'column': columns.get(path), 'code': 'E_VALIDATION', 'value': value,
                                          'message': 'the row names itself as its own parent'})
                continue  # unknown: asked as a decision, or made by a row of this file
            if found in books.ref_index(target)['inactive'] and not (
                    existing is not None and path in books.table.c and found == s.company.conn.execute(
                        sa.select(books.table.c[path]).where(books.table.c.id == existing)).scalar()):
                plan.problems.append({'column': columns.get(path), 'code': 'E_INACTIVE_REFERENCE', 'value': value,
                                      'message': f"{target.replace('-', ' ')} {value!r} is inactive; a new or "
                                                 'changed reference must name an active record'})
        if creating:
            command, body = registry.get(kind.create), probe
        elif existing is not None:
            command = registry.get(kind.update)
            body = {kind.selector: PLACEHOLDER_ID, 'expected_version': 1, **probe}
        else:
            command = None  # update mode with no match: skipped, nothing runs
        if command is not None:
            try:
                validate_input(command, body)
                if creating:
                    _input_rules(s, kind, body)
            except BookflowError as error:
                fields = [f for f in (error.details or {}).get('fields', []) if isinstance(f, dict)] or [{}]
                for f in fields:
                    if f.get('field') in unmapped:
                        continue  # the mapping's own problem, said once for the file
                    column = _column_of(BookflowError(error.code, details={'field': f.get('field')}), columns)
                    path = next((p for p, h in columns.items() if h == column), None)
                    cell = plan.cells[position[path]] if path in position and position[path] < len(plan.cells) else ''
                    problem = f.get('problem') or f.get('message') or error.message
                    if column is None and f.get('field') not in (None, 'input'):
                        problem = f"{f['field']}: {problem}"
                    plan.problems.append({'column': column, 'code': error.code, 'value': cell, 'model': True,
                                          'message': problem})
        _settle(plan)


def _input_rules(s, kind: kinds.Kind, body: dict) -> None:
    """What a create command refuses from its input alone, past its model: a list name with a colon (the colon joins
    a hierarchy's names); an item's fields by its type (a service's income account, an inventory part's costs)."""
    from bookflow.company import lists
    if isinstance(body.get('name'), str) and kind.name != 'sales-rep':
        lists.normalize_display_name(body['name'])
    if kind.name == 'customer' and not body.get('parent_id'):
        # `customer create`'s own rule (parties.py): a top-level customer owns its address and contacts, and has no
        # job fields; the job's columns are filled only on rows naming the customer they belong to.
        for field_ in ('job_start', 'job_projected_end', 'job_end', 'job_description', 'job_type_id',
                       'job_sales_rep_id', 'job_status', 'address_mode', 'contact_mode'):
            value = body.get(field_)
            if value in (None, '') or (field_ == 'job_status' and value == 'none') or (
                    field_ in ('address_mode', 'contact_mode') and value == 'own'):
                continue
            problem = ('a top-level customer owns its address and contacts' if field_.endswith('_mode') else
                       'job fields require a parent customer or job (fill in the column naming its customer)')
            raise BookflowError('E_VALIDATION', message=problem,
                                details={'fields': [{'field': field_, 'problem': problem}]})
    if kind.name == 'item':
        from bookflow.company import items
        parsed = items.parse_item_input(body)
        supplied = set(body)
        parsed = items._determined_accounts(s.company, parsed, supplied)
        parsed = items._service_defaults(s.company, parsed, supplied)
        items._validate_profile(parsed, supplied)


def resolve_payload(books: Books, kind: kinds.Kind, payload: dict, pending: set[str], columns: dict[str, str],
                    decided: dict | None = None, creator=None) -> tuple[dict | None, list[dict], bool, dict | None]:
    """(input with references as ids, every unknown reference, whether a reference waits on an earlier row, the skip
    decision that leaves the row out). A decision made with `import decide` applies first: a map replaces the file's
    text, a create makes the record through ``creator`` (when running), a skip leaves the row out."""
    out = json.loads(json.dumps(payload))
    waits = False
    unknown: list[dict] = []
    for path, target in _ref_paths(kind, out):
        value = out.get(path)
        if not isinstance(value, str) or not value:
            continue
        decision = (decided or {}).get((path, norm_key(value)))
        wanted = value
        if decision is not None and decision['action'] == 'skip':
            return None, [], False, decision
        if decision is not None and decision['action'] == 'map':
            wanted = decision['target']
        found = books.resolve(target, wanted)
        if found is None and decision is not None and decision['action'] == 'create' and creator is not None:
            found = creator(target, path, value, decision)
        if found is None and target == kind.noun and norm_key(wanted) in pending:
            waits = True
            continue
        if found is None:
            unknown.append({'field': path, 'column': columns.get(path), 'target': target, 'value': value,
                            'decision': decision})
            continue
        out[path] = found
    if unknown:
        return None, unknown, False, None
    return out, [], waits, None


def unknown_message(unknown: dict, suggestions: list[str] | None = None) -> str:
    hint = f" Did you mean {', '.join(repr(x) for x in suggestions)}?" if suggestions else ''
    return f"No {unknown['target'].replace('-', ' ')} matches {unknown['value']!r} ({unknown['field']}).{hint}"


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


def before_values(kind: kinds.Kind, current: dict, payload: dict, after: dict | None) -> dict:
    """What an update's reversal sends back to restore the record exactly: the prior value, shaped as the update
    command's input, of every field the row set and every field the update changed beside them (a primary contact
    the phone made, a derived address), read from the record as shown before and after the write."""
    from bookflow.core import registry
    model = registry.get(kind.update).input_model
    out = {}
    for key, info in model.model_fields.items():
        if key in (kind.selector, 'expected_version') or key not in current:
            continue
        prior = _shaped(info.annotation, _plain(current.get(key)))
        if key in payload or (after is not None and key in after
                              and _shaped(info.annotation, _plain(after.get(key))) != prior):
            out[key] = prior
    return out


def _shaped(annotation, value):
    """A shown value as an input of ``annotation`` takes it: nested records keep only their input fields."""
    import typing
    if isinstance(value, dict) and 'amount' in value and 'currency' in value:
        return value['amount']
    candidates = [a for a in (typing.get_args(annotation) or (annotation,)) if a is not type(None)]
    if isinstance(value, list):
        items = [typing.get_args(a)[0] for a in candidates if typing.get_origin(a) is list and typing.get_args(a)]
        return [_shaped(items[0], v) for v in value] if items else value
    if isinstance(value, dict):
        for candidate in candidates:
            if hasattr(candidate, 'model_fields'):
                return {k: _shaped(candidate.model_fields[k].annotation, v) for k, v in value.items()
                        if k in candidate.model_fields}
    return value


# ------------------------------------------------------------------ classifying one row (preview and run alike)

def classify(s, ctx, run: dict, kind: kinds.Kind, books: Books, plan: RowPlan, pending: set[str],
             columns: dict[str, str], *, dry_run: bool, decided: dict | None = None,
             creator=None) -> tuple[str, dict | None, dict | None]:
    """Set the row's status; returns (action, resolved input, the record as shown before an update) where action is
    create, update or none."""
    if kinds.is_document(kind):
        return documents().classify(s, ctx, run, kind, books, plan, columns, dry_run=dry_run, decided=decided,
                                    creator=creator)
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
        _not_all(run, plan, 'skipped')
        return 'none', None, None
    resolved, unknowns, waits, skip = resolve_payload(books, kind, payload, pending, columns, decided, creator)
    if skip is not None:
        plan.status, plan.code = 'skipped', 'skipped_by_decision'
        plan.message = f"Skipped: {skip['value']!r} ({skip['field']}) was decided skip."
        return 'none', None, None
    if unknowns:
        first = unknowns[0]
        # Before the run, an unknown reference asks (`import decide`); once running, an undecided one refuses.
        plan.status = 'waiting' if dry_run else 'refused'
        plan.code, plan.column = 'unknown_reference', first['column']
        plan.message = unknown_message(first, books.suggestions(first['target'], first['value']))
        plan.unknown = unknowns  # type: ignore[attr-defined]
        return 'none', None, None
    if existing is not None:
        plan.record_type, plan.record_id = kind.definition.record_type, existing
        current = batches.run_nested(s, ctx, f'{kind.noun} show', {kind.selector: existing},
                                     source_ref=f"import:{run['id']}")
        if _same(kind, resolved, current):
            plan.status = 'unchanged'
            return 'none', None, None
        if mode == 'create':
            plan.status, plan.code = 'differs', 'differs'
            changed = [p for p in _leaves(resolved) if norm_key(_get_path(resolved, p))
                       != norm_key(_get_path(current, p))]
            plan.message = 'A record with this name exists and differs in ' + ', '.join(changed[:8]) + '; nothing applied.'
            _not_all(run, plan, 'differs')
            return 'none', None, None
        body = {kind.selector: existing, 'expected_version': current['version'], **resolved}
        plan.status = 'update' if dry_run else 'updated'
        return 'update', body, current
    plan.status = 'create' if dry_run else 'created'
    if waits:
        plan.message = 'Refers to a record another row of the file makes; that row runs first.'
        return 'deferred', resolved, None
    return 'create', resolved, None


def _not_all(run: dict, plan: RowPlan, outcome: str) -> None:
    """All or nothing (Kabe 2026-10-27) is total: a row that would leave its record as it is, because the record
    differs or none matches, refuses an atomic run unless the caller accepted that outcome (`allow`)."""
    if run.get('atomic') and outcome not in (run.get('allow') or ()):
        plan.message = (f'{plan.message} All or nothing refuses it; correct the file, or pass allow ["{outcome}"] to '
                        'accept it.')
        plan.status = 'refused'


def _dry_run(s, ctx, run, kind, plan: RowPlan, action: str, body: dict, columns: dict[str, str]) -> None:
    name = kind.create if action == 'create' else kind.update
    try:
        batches.run_nested(s, ctx, name, body, source_ref=f"import:{run['id']}:{plan.row_no}", dry_run=True,
                           recovery=False)
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


CHECK_SHOWN = 1000  # problem groups, dropped characters and unknown references a check lists in full
_ROW_LEVEL = frozenset(('no_key', 'duplicate_in_file', 'row_width'))


def read_table(s, inp) -> tuple[tabular.Table, str | None]:
    """The file of a check or preview, from `content` or an `attachment`, with the attachment's own name."""
    if (inp.content is None) == (inp.attachment is None):
        raise BookflowError('E_VALIDATION', message='Give the file as `content` or `attachment`, one of them.',
                            details={'field': 'content'})
    attachment_name = None
    if inp.attachment is not None:
        row, raw = _attachment_bytes(s, inp.attachment)
        attachment_name = row['original_filename']
    else:
        raw = inp.content
    table = tabular.read(raw, delimiter=inp.delimiter, header_row=inp.header_row, skip_rows=inp.skip_rows,
                         sheet=inp.sheet)
    return table, attachment_name


def mapping_problems(kind: kinds.Kind, mapping: Mapping, mode: str) -> list[dict]:
    """Is the mapping complete? Every field the command requires needs a column or a constant."""
    if mode != 'create':
        return []
    mapped = set(mapping.columns.values()) | set(mapping.constants)
    out = []
    for name in kinds.required(kind.name, mode):
        if not any(p == name or p.startswith((name + '.', name + '[].')) for p in mapped):
            out.append({'column': None, 'code': 'unmapped_required', 'value': '', 'rows': [],
                        'message': f'No column maps to {name}, which `{kind.create}` requires; map one (`columns`) '
                                   'or give it as a constant.'})
    return out


def check_report(kind: kinds.Kind, table: tabular.Table, mapping: Mapping, rows: list[RowPlan], books: 'Books',
                 mode: str, constants: list[dict] = ()) -> dict:
    """Every problem in the file at once, grouped by column, then cell text, with row numbers (Kabe 2026-10-27), the
    characters dropped from text, and the references the books lack. Runs no command and changes nothing."""
    order = {h: i for i, h in enumerate(table.headers)}
    groups: dict[tuple, dict] = {}
    total = 0
    bad_rows: set[int] = set()
    dropped: list[dict] = []
    dropped_total = 0
    for plan in rows:
        dropped_total += len(plan.dropped)
        if len(dropped) < CHECK_SHOWN:
            dropped.extend(plan.dropped[:CHECK_SHOWN - len(dropped)])
        for problem in plan.problems:
            total += 1
            bad_rows.add(problem.get('row', plan.row_no))
            column = problem['column'] or problem.get('key_column')
            row_level = problem['code'] in _ROW_LEVEL or problem.get('required')
            key = (column, problem['code'], '' if row_level else problem['value'],
                   problem['message'] if problem.get('model') else '')
            group = groups.get(key)
            if group is None:
                message = {'duplicate_in_file': 'the key repeats an earlier row\'s',
                           'no_key': 'no value in the key column'}.get(problem['code'], problem['message'])
                group = groups[key] = {'column': column, 'code': problem['code'],
                                       'value': None if row_level else problem['value'], 'message': message,
                                       'rows': []}
            row = problem.get('row', plan.row_no)
            if row not in group['rows']:
                group['rows'].append(row)
    listed = mapping_problems(kind, mapping, mode) + list(constants) + sorted(
        groups.values(), key=lambda g: (order.get(g['column'], len(order)), g['rows'][0]))
    # References: one lookup per list, held in memory (Books); a value the books lack asks, once per value.
    columns = {p: table.headers[i] for i, p in mapping.columns.items()}
    pending = file_names(books, rows)
    unknown: dict[tuple, dict] = {}
    for plan in rows:
        payload = plan.payload or getattr(plan, 'checked', None)
        if not payload:
            continue
        wrong = {p['column'] for p in plan.problems}
        found = (documents().resolve_payload(books, kind, payload, columns, line_rows=plan.line_rows)
                 if kinds.is_document(kind) else
                 resolve_payload(books, kind, payload, _Others(pending, norm_key(books.selector(payload) or '')),
                                 columns))
        for u in found[1]:
            if u['column'] is not None and u['column'] in wrong:
                continue  # that cell is already a problem (its own parent, say)
            entry = unknown.get((u['field'], norm_key(u['value'])))
            if entry is None:
                entry = unknown[(u['field'], norm_key(u['value']))] = {
                    'field': u['field'], 'column': u['column'], 'target': u['target'], 'value': u['value'],
                    'rows': [], 'suggestions': books.suggestions(u['target'], u['value']),
                    'look_alikes': books.look_alikes(u['target'], u['value']), 'create': creatable(u['target'])}
                if plan.originals.get(u['field']):
                    entry['file_value'] = plan.originals[u['field']]  # before a value map changed it
            row = u.get('row') or (plan.spans[0][0] if plan.spans else plan.row_no)
            if row not in entry['rows']:
                entry['rows'].append(row)
    summary = []
    for g in listed[:CHECK_SHOWN]:
        rows_text = (', '.join(str(r) for r in g['rows'][:20]) + (f' and {len(g["rows"]) - 20} more'
                                                                    if len(g['rows']) > 20 else ''))
        where = (f"{g['column'] or 'Row'}, row{'s' if len(g['rows']) > 1 else ''} {rows_text}: " if g['rows']
                 else '')
        summary.append(where + g['message'])
    # Kabe 2026-10-27: the check catches what the run would refuse. What the file needs decided (a decimal mark, a
    # date order, which saved mapping, each unknown reference) is not a problem in a cell, but nothing runs before it
    # is decided: the check is not ok until then, and lists each.
    to_decide = [{'decision': d['decision'], 'message': d['message']} for d in mapping.decisions] + [
        {'decision': 'reference', 'message': unknown_message(u, u['suggestions']) + ' Map, create or skip it.',
         'field': u['field'], 'column': u['column'], 'value': u['value'], 'rows': u['rows'][:50]}
        for u in list(unknown.values())[:CHECK_SHOWN]]
    file_rows = sum(len(p.spans) for p in rows) if kinds.is_document(kind) else len(rows)
    extra = {'documents': len(rows), 'documents_with_problems': sum(1 for p in rows if p.problems)} \
        if kinds.is_document(kind) else {}
    return {'ok': not listed and not to_decide, 'clean': not listed, 'rows': file_rows, **extra,
            'rows_with_problems': len(bad_rows), 'to_decide': to_decide,
            'problems_total': total + sum(1 for g in listed if not g['rows']), 'problems': listed[:CHECK_SHOWN],
            'summary': summary, 'dropped': dropped, 'dropped_total': dropped_total,
            'unknown_references': list(unknown.values())[:CHECK_SHOWN]}


class _Others:
    """The names a file's rows make, but this row's own: a membership test, without copying the set per row."""

    def __init__(self, names, own: str):
        self.names, self.own = names, own

    def __contains__(self, key) -> bool:
        return key != self.own and key in self.names


class _NotFinished:
    def __init__(self, waiting_on: dict, finished: set):
        self.waiting_on, self.finished = waiting_on, finished

    def __contains__(self, key) -> bool:
        row = self.waiting_on.get(key)
        return row is not None and row['row_no'] not in self.finished


def file_names(books: 'Books', rows: list[RowPlan]) -> set[str]:
    """What the file's rows name (its records' selectors): a reference to one is made by the file, not unknown."""
    out = set()
    for plan in rows:
        payload = plan.payload if plan.payload is not None else getattr(plan, 'checked', None)
        text = books.selector(payload) if payload else None
        if text:
            out.add(norm_key(text))
    return out


def creatable(target: str) -> str:
    """Whether `import decide --create` may make a missing reference: never an account or item (§2); from the name
    alone when its create command needs only a name; otherwise with the rest of that command's input."""
    if target in ('account', 'item'):
        return 'never'
    from bookflow.core import registry
    model = registry.get(f'{target} create').input_model
    needed = {n for n, f in model.model_fields.items() if f.is_required()}
    return 'from the name' if needed <= {'name'} else 'with input: ' + ', '.join(sorted(needed - {'name'}))


def _prepare(s, ctx, inp) -> dict:
    """Read, match a saved mapping, map and check a file: what `import check` returns and `import preview` starts
    from."""
    from bookflow.company import import_mappings
    kind = kinds.kind(inp.kind)
    # A check or preview shows which records exist and how they differ: the kind's own read permission, first.
    batches.require_read(s, f'{kind.noun} show')
    if kinds.is_document(kind) and inp.mode != 'create':
        raise BookflowError('E_VALIDATION', message=(
            f'A {kind.name.replace("-", " ")} import creates documents only; change one with `{kind.noun} update`.'),
            details={'field': 'mode', 'value': inp.mode})
    table, attachment_name = read_table(s, inp)
    saved, matched, decision = import_mappings.for_file(s, kind, table, inp)
    mapping = build_mapping(kind, table, inp, saved)
    if decision is not None:
        mapping.decisions.append(decision)
    source = inp.source or (saved['source'] if saved else None) or f'{kind.name} import'
    places, year = _places(s), _company_year(s)
    constants = check_constants(kind, mapping, places, year)
    if kinds.is_document(kind):
        rows = documents().plan_documents(kind, table, mapping, source, places, year)
        books = books_for(s, kind, source)
        documents().check_documents(s, kind, table, mapping, rows, books)
    else:
        rows = plan_rows(kind, table, mapping, source, places, year, inp.mode)
        books = Books(s, kind, source)
        check_rows(s, kind, table, mapping, rows, books, inp.mode)
    report = check_report(kind, table, mapping, rows, books, inp.mode, constants)
    notes = []
    if table.encoding_guessed:
        notes.append(f'encoding: {table.encoding} (guessed); save the file as UTF-8 if any name reads wrongly')
    if table.sheet:
        notes.append(f'sheet: {table.sheet!r} (values as last saved; formulas are not evaluated)')
    if mapping.keyed_by_content:
        notes.append('keyed by content: a re-run is safe only for unchanged rows')
        if kinds.is_document(kind) and not getattr(inp, 'key_by_content', False):
            notes.append(f'no key column is mapped ({", ".join(kind.key_default) or "this kind has none"}), so each row '
                         'is a document keyed by its content; a run needs key_by_content, since an edited amount '
                         'would import a second copy')
    if report['dropped_total']:
        notes.append(f"{report['dropped_total']} character(s) no text field holds were dropped (`dropped` lists each)")
    for d in mapping.decisions:
        notes.append(d['message'])
    return {'kind': kind, 'table': table, 'mapping': mapping, 'rows': rows, 'source': source, 'books': books,
            'check': report, 'matched': matched, 'notes': notes, 'attachment_name': attachment_name}


def samples(table: tabular.Table, count: int = 3) -> dict[str, list[str]]:
    """Up to ``count`` non-blank values of each column, as the file has them (cut at 60 characters)."""
    out: dict[str, list[str]] = {h: [] for h in table.headers}
    for _, cells in table.rows:
        for header, cell in zip(table.headers, cells):
            values = out[header]
            if len(values) < count and cell.strip() and cell not in values:
                values.append(cell if len(cell) <= 60 else cell[:59] + '…')
        if all(len(v) >= count for v in out.values()):
            break
    return out


def check(s, ctx, inp) -> dict:
    p = _prepare(s, ctx, inp)
    report = p['check']
    return {**report, 'kind': p['kind'].name, 'source': p['source'], 'matched': p['matched'],
            'mapping': p['mapping'].as_dict(p['table'].headers), 'decisions': p['mapping'].decisions,
            'notes': p['notes'], 'samples': samples(p['table']),
            'choices': reference_choices(p['books'], {u['target'] for u in report['unknown_references']}),
            'next': ('import preview with the same file' + (' and mapping' if p['matched'] else '')) if report['ok']
            else ('decide what `to_decide` lists (preview, then `import decide`, or pass the format)'
                  if report['clean'] else None)}


CHOICES = 2000  # names offered to map an unknown reference to; a longer list is typed (and checked) instead


def reference_choices(books: 'Books', targets) -> dict[str, list[str]]:
    """The active records of each list an unknown reference may map to, by name, for a searchable pick: only lists
    the caller may read (the same rule as the suggestions)."""
    out = {}
    for target in sorted(targets):
        if not books.readable(target):
            continue
        index = books.ref_index(target)
        names = sorted({n for n, i in zip(index['names'], index['order']) if isinstance(n, str)
                        and i not in index['inactive']}, key=str.casefold)
        out[target] = names[:CHOICES]
    return out


def refuse_on_check(report: dict, *, partial: bool, decisions: list[dict] = ()) -> None:
    """All or nothing (Kabe 2026-10-27): a file with any problem is refused whole before any command runs; with
    `partial` only an incomplete mapping is, and each bad row is refused alone."""
    fatal = [g for g in report['problems'] if not g['rows']]
    if not report['clean'] and (fatal or not partial):
        shown = '; '.join(m.rstrip('.') for m in [d['message'] for d in decisions if d['decision'] == 'mapping']
                          + report['summary'][:5])
        more = f" ({report['problems_total']} in all)" if report['problems_total'] > 5 else ''
        what = (f"The file has problems in {report['rows_with_problems']} of {report['rows']} rows{more}"
                if report['rows_with_problems'] else 'The mapping is incomplete')
        raise BookflowError('E_VALIDATION', message=(
            f"{what}, so nothing was imported: {shown}. `import check` lists every one; correct it and check again"
            + ('.' if fatal else ', or pass partial to import the other rows.')),
            details={'field': 'content', 'check': {k: report[k] for k in ('rows', 'rows_with_problems',
                                                                         'problems_total', 'problems', 'summary')}})


def prepare_preview(s, ctx, inp) -> dict:
    p = _prepare(s, ctx, inp)
    kind, table, mapping, rows, books, source = (p['kind'], p['table'], p['mapping'], p['rows'], p['books'],
                                                 p['source'])
    refuse_on_check(p['check'], partial=inp.partial, decisions=mapping.decisions)
    run = {'id': new_id(), 'mode': inp.mode, 'kind': kind.name, 'atomic': not inp.partial, 'allow': list(inp.allow)}
    columns = {path: table.headers[i] for i, path in mapping.columns.items()}
    names = file_names(books, rows)  # a record any row makes, before or after: the run makes that row first
    for plan in rows:
        own = norm_key(books.selector(plan.payload) or '') if plan.payload else ''
        action, body, _ = classify(s, ctx, run, kind, books, plan, _Others(names, own), columns, dry_run=True)
        if action in ('create', 'update'):
            _dry_run(s, ctx, run, kind, plan, action, body, columns)
    references = []
    for u in p['check']['unknown_references']:
        rows_ = [plan.spans[0][0] if plan.spans else plan.row_no for plan in rows if plan.status == 'waiting' and any(
            x['field'] == u['field'] and norm_key(x['value']) == norm_key(u['value'])
            for x in getattr(plan, 'unknown', ()))]
        if rows_:
            references.append({**u, 'rows': rows_[:50], 'row_count': len(rows_)})
    create = _create_missing(books, references, p['notes']) if getattr(inp, 'create_missing', False) else []
    return {'kind': kind, 'table': table, 'mapping': mapping, 'rows': rows, 'run_id': run['id'], 'source': source,
            'create': create, 'attachment': inp.attachment, 'file_name': inp.file_name or p['attachment_name'], 'notes': p['notes'],
            'input': inp, 'columns': columns, 'references': references, 'matched': p['matched'],
            'dropped': p['check']['dropped'], 'dropped_total': p['check']['dropped_total']}


def apply_preview(s, ctx, prepared: dict) -> dict:
    """Store the run and its preview rows (import tables only, never the books) in the caller's transaction."""
    table, mapping, inp = prepared['table'], prepared['mapping'], prepared['input']
    at = batches.now()
    from bookflow.company import import_mappings
    s.company.conn.execute(sa.insert(c.import_runs).values(
        id=prepared['run_id'], kind=prepared['kind'].name, mode=inp.mode, atomic=not inp.partial,
        state='waiting' if len(prepared['references']) > len(prepared['create']) else 'previewed',
        source=prepared['source'], mapping=json.dumps({
            **mapping.as_dict(table.headers), 'headers': table.headers, 'notes': prepared['notes'],
            'decisions': mapping.decisions, 'references': prepared['references'], 'matched': prepared['matched'],
            'fingerprint': import_mappings.fingerprint(table.headers), 'sheet': table.sheet, 'allow': list(inp.allow),
            'dropped': prepared['dropped'], 'dropped_total': prepared['dropped_total'],
            'key_by_content': bool(getattr(inp, 'key_by_content', False))}, sort_keys=True),
        attachment_id=prepared['attachment'], file_name=prepared['file_name'], file_sha256=table.sha256,
        encoding=table.encoding, delimiter=table.delimiter, row_count=len(prepared['rows']),
        stop_after_refusals=inp.stop_after_refusals, batch_id=None, reason=ctx.reason, version=1, created_at=at,
        created_by=s.actor.id, created_via=str(getattr(ctx.interface, 'value', ctx.interface))[:16], updated_at=at))
    values = [_row_values(prepared['run_id'], plan, 'preview', at, keep=True) for plan in prepared['rows']]
    for start in range(0, len(values), 500):
        s.company.conn.execute(sa.insert(c.import_rows), values[start:start + 500])
    for ref in prepared['create']:
        s.company.conn.execute(sa.insert(c.import_decisions).values(
            id=new_id(), run_id=prepared['run_id'], field=ref['field'], value=ref['value'],
            value_key=norm_key(ref['value']), action='create', target=ref['value'], input=None, remembered=False,
            created_at=at, created_by=s.actor.id))
    return show(s, ctx, prepared['run_id'], prepared=prepared)


def _create_missing(books, references: list[dict], notes: list[str]) -> list[dict]:
    """`create_missing` (§2): the unknown references an import may make from their name alone, decided `create` at
    the preview and made by their own command when their first row runs. A customer or vendor that looks like an
    existing one is never made, whoever runs the import: it stays to decide (map it, or create it with its own
    command). An item or account is never made; a list needing more than a name (terms) stays to decide too."""
    out = []
    for ref in references:
        target = ref['target']
        alike = books.look_alikes(target, ref['value']) if target in ('customer', 'vendor') else []
        if alike:
            notes.append(f"{ref['value']!r} was not created: it looks like {', '.join(map(repr, alike))}; map it to "
                         'one of them, or skip its rows.')
        elif creatable(target) == 'from the name':
            out.append(ref)
    return out


def _row_values(run_id: str, plan: RowPlan, phase: str, at: str, *, keep: bool = False) -> dict:
    return {'run_id': run_id, 'row_no': plan.row_no, 'phase': phase, 'line': plan.line, 'key_hash': plan.key_hash,
            'status': plan.status, 'code': plan.code, 'message': plan.message, 'column_name': plan.column,
            'record_type': plan.record_type, 'record_id': plan.record_id,
            'payload': json.dumps(plan.payload, sort_keys=True) if keep and plan.payload is not None else None,
            'cells': json.dumps(plan.cells) if keep else None, 'created_at': at,
            'spans': json.dumps(plan.spans) if keep and plan.spans else None}


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


def _phase(run: dict) -> str:
    return 'preview' if run['state'] in ('previewed', 'waiting') else 'run'


def decided(s, run_id: str) -> dict[tuple[str, str], dict]:
    """The run's decisions about unknown references, by (field, normalized file text)."""
    out = {}
    for row in s.company.conn.execute(sa.select(c.import_decisions).where(
            c.import_decisions.c.run_id == run_id)).mappings():
        out[(row['field'], row['value_key'])] = {'field': row['field'], 'value': row['value'],
                                                 'action': row['action'], 'target': row['target'],
                                                 'input': json.loads(row['input']) if row['input'] else {}}
    return out


def pending_references(s, run: dict, mapping: dict | None = None) -> list[dict]:
    """The unknown references of a run's preview not yet decided."""
    mapping = mapping if mapping is not None else json.loads(run['mapping'])
    done = decided(s, run['id'])
    return [r for r in mapping.get('references', []) if (r['field'], norm_key(r['value'])) not in done]


def _counts(s, run_id: str, phase: str) -> dict[str, int]:
    rows = s.company.conn.execute(sa.select(c.import_rows.c.status, sa.func.count()).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == phase).group_by(c.import_rows.c.status)).all()
    return {status: int(n) for status, n in rows}


def run_slice(s, ctx, inp, *, interrupt=None) -> dict:
    """Run the next slice of a previewed run. ``interrupt`` is a test hook called after each row."""
    run = _run_row(s, inp.run)
    kind = kinds.kind(run['kind'])
    if run['state'] in ('cancelled', 'rolled_back', 'failed'):
        raise BookflowError('E_VALIDATION', message=f"Run {run['id']} is {run['state']}; preview the file again.",
                            details={'field': 'run', 'state': run['state']})
    if run['state'] == 'done':
        return show(s, ctx, run['id'])
    mapping = json.loads(run['mapping'])
    run['allow'] = mapping.get('allow') or []
    if mapping.get('decisions'):
        raise BookflowError('E_VALIDATION', message=('The preview needs decisions first: '
                                                     + '; '.join(d['message'] for d in mapping['decisions'])
                                                     + ' Preview again with them.'),
                            details={'field': 'run', 'decisions': mapping['decisions']})
    waiting = pending_references(s, run, mapping)
    if waiting:
        raise BookflowError('E_VALIDATION', message=(
            f"The run waits on {len(waiting)} unknown reference(s): "
            + '; '.join(f"{r['value']!r} ({r['column'] or r['field']})" for r in waiting[:5])
            + f". Decide each with `import decide {run['id']}` (map, create or skip)."),
            details={'field': 'run', 'decisions': waiting[:50]})
    if run['atomic'] and _phase(run) == 'preview' and _counts(s, run['id'], 'preview').get('refused'):
        raise BookflowError('E_VALIDATION', message=('An atomic run starts only when its preview refused no rows; '
                                                     'correct the file and preview again.'),
                            details={'field': 'run', 'refused': _counts(s, run['id'], 'preview')['refused']})
    if run['batch_id'] is not None:
        state = s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == run['batch_id'])).scalar()
        if state != 'live':
            raise BookflowError('E_VALIDATION', message=(f"Run {run['id']}'s batch is {state}; a reversed run is never "
                                                         'continued. Preview the corrected file again.'),
                                details={'field': 'run', 'batch_state': state})
    if kinds.is_document(kind) and mapping.get('keyed_by_content') and not (
            mapping.get('key_by_content') or getattr(inp, 'key_by_content', False)):
        raise BookflowError('E_VALIDATION', message=(
            f"No key column is mapped, so each row is a {kind.name.replace('-', ' ')} keyed by its content: a re-run "
            'after an amount is edited would import a second copy. Map the key columns ('
            + (', '.join(kind.key_default) or 'this kind has none') + ') and preview again, or run with key_by_content '
            'to accept it.'), details={'field': 'key_by_content', 'run': run['id']})
    columns = {p: h for h, p in mapping['columns'].items()}
    books = books_for(s, kind, run['source'])
    choices = decided(s, run['id'])
    if run['batch_id'] is None and run['atomic']:
        _recheck(s, run, kind, books, columns, choices)
    if run['batch_id'] is None:
        _start(s, ctx, run, kind)
    elif run['state'] != 'running':
        _update_run(s, run, state='running')
    creator = _reference_creator(s, ctx, run, books)
    deadline = time.monotonic() + inp.seconds
    refused_now = done_now = 0
    buffered: list[dict] = []
    stopped = False
    # A row may refer to a record a later row of the file makes (a job listed before its customer): that row runs
    # first, then this one. `waiting_on` finds the row by what it makes, among the rows not yet run.
    waiting_on = {}
    for stored in (_pending_rows(s, run['id'], 10 ** 9) if kind.noun in _references(kind).values() else ()):
        payload = json.loads(stored['payload']) if stored['payload'] else None
        text = books.selector(payload) if payload else None
        if text:
            waiting_on.setdefault(norm_key(text), stored)
    finished: set[int] = set()

    def process(stored: dict, chain: tuple = ()) -> RowPlan:
        nonlocal buffered
        finished.add(stored['row_no'])
        plan = RowPlan(stored['row_no'], stored['line'], json.loads(stored['cells'] or '[]'),
                       json.loads(stored['payload']) if stored['payload'] else None, stored['key_hash'])
        plan.spans = json.loads(stored['spans']) if stored.get('spans') else []
        if stored['status'] == 'refused' and (stored['payload'] is None or stored['code'] in READER_CODES):
            plan.status, plan.code, plan.message, plan.column = 'refused', stored['code'], stored['message'], \
                stored['column_name']
            action, body, before = 'none', None, None
        else:
            creator.row = plan.row_no
            own = norm_key(books.selector(plan.payload) or '') if plan.payload else ''
            later = _Others(_NotFinished(waiting_on, finished), own)
            action, body, before = classify(s, ctx, run, kind, books, plan, later, columns, dry_run=False,
                                            decided=choices, creator=creator)
        if action in ('create', 'update', 'deferred'):
            command = kind.update if action == 'update' else kind.create
            plan.status = 'updated' if action == 'update' else 'created'
            if action == 'deferred':
                for u in resolve_payload(books, kind, plan.payload or {}, set(), columns, choices, creator)[1]:
                    first = waiting_on.get(norm_key(u['value'])) if u['target'] == kind.noun else None
                    if first is not None and first['row_no'] not in finished and len(chain) < 100:
                        made = process(first, chain + (plan.row_no,))  # the row that makes it, first
                        if made.status == 'refused' and run['atomic']:
                            raise _Refused(made)
                body, unknowns, _, _ = resolve_payload(books, kind, plan.payload or {}, set(), columns, choices,
                                                       creator)
                if unknowns:
                    plan.status, plan.code, plan.column = 'refused', 'unknown_reference', unknowns[0]['column']
                    plan.message = unknown_message(unknowns[0])
                    body = None
            if body is not None:
                _insert_rows(s, buffered)
                buffered = []
                try:
                    output = batches.run_nested(s, ctx, command, body,
                                                source_ref=f"import:{run['id']}:{plan.row_no}",
                                                recorder=_row_recorder(run, kind, plan, before),
                                                recovery=not kinds.is_document(kind))
                except BookflowError as error:
                    plan.status, plan.code, plan.message = 'refused', error.code, _message(error)
                    plan.column = _column_of(error, columns)
                    plan.record_type = plan.record_id = None
                else:
                    if action != 'update':
                        made_id = output.get('id') or plan.record_id
                        books.made(plan.payload or {}, made_id)
                        books.ledger[plan.key_hash] = (kind.definition.record_type, made_id)
        if plan.status not in WRITES:
            buffered.append(_row_values(run['id'], plan, 'run', batches.now()))
        if plan.status == 'refused' and run['atomic']:
            raise _Refused(plan)
        return plan

    for stored in _pending_rows(s, run['id'], inp.limit):
        if stored['row_no'] in finished:
            continue  # ran already, ahead of its turn, for a row that needed it
        try:
            plan = process(stored)
        except _Refused as refused:
            _insert_rows(s, buffered)
            return _roll_back(s, ctx, run, refused.plan)
        done_now += 1
        if plan.status == 'refused':
            refused_now += 1
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


class _Refused(Exception):
    """A row refused in an all-or-nothing run: the run undoes itself."""

    def __init__(self, plan: RowPlan):
        super().__init__(plan.code)
        self.plan = plan


def _recheck(s, run: dict, kind: kinds.Kind, books: Books, columns: dict[str, str], choices: dict) -> None:
    """An all-or-nothing run starts with the check's half that the books can change since the preview: every
    reference still found (or decided). The file's own half cannot change: the run holds what was previewed. A
    reference gone since refuses the run before it writes anything."""
    stored = s.company.conn.execute(sa.select(c.import_rows.c.row_no, c.import_rows.c.payload).where(
        c.import_rows.c.run_id == run['id'], c.import_rows.c.phase == 'preview',
        c.import_rows.c.status.in_(('create', 'update', 'waiting')))).all()
    payloads = [(row_no, json.loads(payload)) for row_no, payload in stored if payload]
    pending = {norm_key(books.selector(p)) for _, p in payloads if books.selector(p)}
    unknown: dict[tuple, dict] = {}
    for row_no, payload in payloads:
        _, found, _, _ = (documents().resolve_payload(books, kind, payload, columns, choices)
                          if kinds.is_document(kind) else resolve_payload(books, kind, payload, pending, columns, choices))
        for u in found:
            if u['decision'] is not None and u['decision']['action'] == 'create':
                continue  # made when its first row runs
            entry = unknown.setdefault((u['field'], norm_key(u['value'])), {**u, 'rows': []})
            entry['rows'].append(row_no)
    if unknown:
        listed = sorted(unknown.values(), key=lambda u: u['rows'][0])
        raise BookflowError('E_VALIDATION', message=(
            'Since the preview, the books lost what these rows refer to, so nothing was imported: '
            + '; '.join(f"{u['column'] or u['field']}, rows {', '.join(map(str, u['rows'][:10]))}: no "
                        f"{u['target'].replace('-', ' ')} {u['value']!r}" for u in listed[:5])
            + '. Preview the file again.'),
            details={'field': 'run', 'check': [{k: u[k] for k in ('field', 'column', 'target', 'value', 'rows')}
                                               for u in listed[:CHECK_SHOWN]]})


def _reference_creator(s, ctx, run: dict, books: Books):
    """Makes a reference decided `create` when its first row runs: its own create command, nested as the rows are,
    with its own audit event, recorded in the run's batch so the run's reversal retires it too."""
    from bookflow.company import lists

    def create(target: str, path: str, value: str, decision: dict) -> str | None:
        definition = lists.get_list_definition(target)
        body = {**(decision.get('input') or {}), 'name': decision.get('target') or value}

        def record(s_, cmd, applied, output):
            if isinstance(output, dict) and output.get('id'):
                batches.record_effect(s_, run['batch_id'], record_type=definition.record_type,
                                      record_id=output['id'], action='created', version_after=output.get('version'),
                                      before=None, event_id=batches.last_event_id(s_))
        try:
            output = batches.run_nested(s, ctx, f'{target} create', body,
                                        source_ref=f"import:{run['id']}:{create.row}", recorder=record)
        except BookflowError:
            return None  # the row is refused as an unknown reference; `import decide` dry-ran this create
        books.add_reference(target, body['name'], output['id'])
        return output['id']
    create.row = 0
    return create


def _start(s, ctx, run: dict, kind: kinds.Kind) -> None:
    """Open the run's batch and mark the run `running` in one transaction."""
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            batch_id = batches._insert_batch(
                s, ctx, kind='import', label=f"Import of {run['file_name'] or kind.name + 's'} ({run['row_count']} rows)",
                scope=f'import:{kind.name}', ordering=batches.adapter_for('import').ordering, source_type='import_run',
                source_id=run['id'], reason=ctx.reason or run['reason'])
            changed = s.company.conn.execute(sa.update(c.import_runs).where(
                c.import_runs.c.id == run['id'], c.import_runs.c.batch_id.is_(None)).values(
                batch_id=batch_id, state='running', version=c.import_runs.c.version + 1, updated_at=batches.now()))
            if changed.rowcount != 1:
                raise BookflowError('E_VERSION_CONFLICT', message=f"Run {run['id']} began meanwhile; run it again.",
                                    details={'field': 'run'})
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    run.update(batch_id=batch_id, state='running')


def _row_recorder(run: dict, kind: kinds.Kind, plan: RowPlan, before: dict | None):
    record_type = kind.definition.record_type

    def record(s, cmd, applied, output):
        event_id = batches.last_event_id(s)
        if kinds.is_document(kind):
            # A document made: its id, or each payment a bill payment wrote (one per vendor and account).
            made = [(output['id'], output.get('version'))] if output.get('id') else [
                (p['id'], p.get('version')) for p in output.get('payments') or [] if p.get('id')]
            for rid, version in made:
                batches.record_effect(s, run['batch_id'], record_type=record_type, record_id=rid, action='created',
                                      version_after=version, before=None, event_id=event_id)
            main = made[0][0] if made else None
            if main:
                s.company.conn.execute(sa.insert(c.import_keys).values(
                    id=new_id(), source=run['source'], key_hash=plan.key_hash, record_type=record_type,
                    record_id=main, run_id=run['id'], batch_id=run['batch_id'], created_at=batches.now()))
            plan.record_type, plan.record_id = record_type, main
            s.company.conn.execute(sa.insert(c.import_rows).values(_row_values(run['id'], plan, 'run',
                                                                               batches.now())))
            return
        found = batches.touched_effects(applied, {record_type})
        if not found and isinstance(output, dict) and output.get('id'):
            found = [(record_type, output['id'], 'created' if cmd.name == kind.create else 'updated',
                      output.get('version'))]
        main = output.get('id') if isinstance(output, dict) else None
        for rt, rid, action, version in found:
            if rid != main:
                continue
            prior = (before_values(kind, before, plan.payload or {}, output if isinstance(output, dict) else None)
                     if action == 'updated' and before is not None else None)
            batches.record_effect(s, run['batch_id'], record_type=rt, record_id=rid, action=action,
                                  version_after=version, before=prior, event_id=event_id)
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
    try:
        if made:
            batches.reverse(s, ctx, run['batch_id'], reason=f'Atomic import stopped at row {plan.row_no}: {plan.code}',
                            limit=10 ** 9)
    except BookflowError as error:
        # The undo was refused: the plan is checked whole first, so it changed nothing (or, refused part way, what
        # it undid is recorded and `import reverse` goes on). The run ends `failed` and is never continued.
        _update_run(s, run, state='failed')
        out = show(s, ctx, run['id'])
        out['notes'].append(f"Row {plan.row_no} was refused ({plan.code}: {plan.message}); undoing the "
                            f"{sum(made.values())} record(s) the atomic run had made was refused too: {error.message} "
                            f"`import reverse {run['id']}` finishes it once that is cleared.")
        return out
    _update_run(s, run, state='rolled_back')
    out = show(s, ctx, run['id'])
    out['notes'].append(f'Row {plan.row_no} was refused ({plan.code}: {plan.message}); the atomic run undid the '
                        f'{sum(made.values())} record(s) it had made.')
    return out


def cancel(s, ctx, run_id: str) -> dict:
    run = _run_row(s, run_id)
    if run['state'] in ('done', 'cancelled', 'rolled_back', 'failed'):
        raise BookflowError('E_VALIDATION', message=f"Run {run_id} is already {run['state']}.",
                            details={'field': 'run', 'state': run['state']})
    live = run['batch_id'] is not None and s.company.conn.execute(sa.select(c.batches.c.state).where(
        c.batches.c.id == run['batch_id'])).scalar() == 'live'
    made = batches.counts(s, run['batch_id']) if live else {}
    if run['atomic'] and made:
        # All or nothing: a cancelled run lands nothing, so what its slices made (and any reference a decision
        # created) is undone through its batch first. A refused undo leaves the run as it was.
        batches.reverse(s, ctx, run['batch_id'], reason=ctx.reason or f'Import run {run_id} cancelled', limit=10 ** 9)
        run = _run_row(s, run_id)
    _update_run(s, run, state='cancelled')
    out = show(s, ctx, run_id)
    if run['atomic'] and made:
        out['notes'].append(f'Cancelled; the {sum(made.values())} record(s) the run had made were undone, as all or '
                            'nothing lands none.')
    return out


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

def _run_read(s, run_id: str) -> dict:
    """The run, if the caller may read its kind's records: its cells and refusals are those records' values."""
    run = _run_row(s, run_id)
    batches.require_read(s, f"{kinds.kind(run['kind']).noun} show")
    return run


def show(s, ctx, run_id: str, *, done_now: int = 0, prepared: dict | None = None) -> dict:
    run = _run_read(s, run_id)
    mapping = json.loads(run['mapping'])
    phase = _phase(run)
    counts = _counts(s, run_id, phase)
    if phase == 'run':
        counts['not_run'] = run['row_count'] - sum(counts.values())
    refused = [_row_out(r) for r in s.company.conn.execute(sa.select(c.import_rows).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == phase, c.import_rows.c.status == 'refused')
        .order_by(c.import_rows.c.row_no).limit(REFUSED_SHOWN)).mappings()]
    sample = []
    choices = decided(s, run_id)
    unknown = [{**r, 'decided': choices.get((r['field'], norm_key(r['value'])))}
               for r in mapping.get('references', [])]
    waiting = [dict(r, decision='reference', message=(
        f"No {r['target'].replace('-', ' ')} {r['value']!r} ({r['column'] or r['field']}, {r['row_count']} row(s)): "
        f"map it to an existing one, create it ({r['create']}) or skip its rows")) for r in unknown
        if r['decided'] is None]
    kind = kinds.kind(run['kind'])
    if prepared is not None:
        for plan in prepared['rows']:
            if plan.status in ('create', 'update') and len(sample) < SAMPLE:
                sample.append({'row': plan.row_no, 'command': prepared['kind'].create if plan.status == 'create'
                               else prepared['kind'].update, 'input': plan.payload})
    elif phase == 'preview':  # a reload of the preview shows the same sample, from the rows it stored
        for r in s.company.conn.execute(sa.select(c.import_rows.c.row_no, c.import_rows.c.status,
                                                  c.import_rows.c.payload).where(
                c.import_rows.c.run_id == run_id, c.import_rows.c.phase == 'preview',
                c.import_rows.c.status.in_(('create', 'update')), c.import_rows.c.payload.is_not(None))
                .order_by(c.import_rows.c.row_no).limit(SAMPLE)):
            sample.append({'row': r[0], 'command': kind.create if r[1] == 'create' else kind.update,
                           'input': json.loads(r[2])})
    batch = None
    if run['batch_id']:
        b = s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == run['batch_id'])).scalar()
        batch = {'id': run['batch_id'], 'state': b}
    next_ = None
    if run['state'] in ('previewed', 'waiting', 'running', 'stopped'):
        next_ = f'import run {run_id}'
        if waiting:
            next_ = f'import decide {run_id} (map, create or skip each unknown reference)'
        if mapping.get('decisions'):
            next_ = 'import preview again with ' + ', '.join(d['decision'] for d in mapping['decisions'])
    elif run['state'] == 'done' and counts.get('refused'):
        next_ = f'import errors {run_id}'
    if prepared is not None and len(prepared['rows']) > 100:
        mapping['notes'] = list(mapping.get('notes', [])) + ['Over 100 rows: consider `company backup` first.']
    from bookflow.company.info import principal_names
    by = principal_names(s.company, {run['created_by']}).get(run['created_by'])
    return {
        'outlook': outlook(s, run, kind, mapping, counts, choices) if phase == 'preview' else {},
        'run': {'id': run['id'], 'kind': run['kind'], 'mode': run['mode'], 'atomic': run['atomic'],
                'state': run['state'], 'source': run['source'], 'file_name': run['file_name'],
                'attachment': run['attachment_id'], 'file_sha256': run['file_sha256'], 'encoding': run['encoding'],
                'delimiter': tabular.DELIMITER_NAMES.get(run['delimiter'], run['delimiter']),
                'rows': run['row_count'], 'batch': batch, 'reason': run['reason'], 'created_at': run['created_at'],
                'created_by': run['created_by'], 'created_by_name': by, 'created_via': run['created_via'], 'updated_at': run['updated_at'],
                'version': run['version']},
        'counts': counts,
        'mapping': {k: mapping.get(k) for k in ('columns', 'found_by', 'unmapped', 'key', 'keyed_by_content',
                                                 'formats', 'constants', 'value_maps', 'saved', 'sheet')},
        'unknown_references': unknown,
        'decisions': mapping.get('decisions', []) + waiting,
        'matched': mapping.get('matched'),
        'dropped': mapping.get('dropped', []),
        'dropped_total': mapping.get('dropped_total', 0),
        'refused': refused,
        'sample': sample,
        'notes': list(mapping.get('notes', [])),
        'done_now': done_now,
        'next': next_,
    }


def outlook(s, run: dict, kind: kinds.Kind, mapping: dict, counts: dict, choices: dict) -> dict:
    """What a run of this preview would land, row by row, with the answers given so far: the confirmation counts
    only these. A row waiting on an unknown reference lands once each of its references is answered with a map or a
    create (`answered`), is left out by a skip (`left_out`), and otherwise still waits (`waiting`)."""
    out = {'create': counts.get('create', 0), 'update': counts.get('update', 0), 'answered': 0, 'left_out': 0,
           'waiting': 0, 'unchanged': sum(counts.get(k, 0) for k in ('unchanged', 'exists', 'already_in')),
           'differs': counts.get('differs', 0), 'missing': counts.get('missing', 0),
           'refused': counts.get('refused', 0)}
    if counts.get('waiting'):
        unknown = {(r['field'], norm_key(r['value'])) for r in mapping.get('references', [])}
        for (payload,) in s.company.conn.execute(sa.select(c.import_rows.c.payload).where(
                c.import_rows.c.run_id == run['id'], c.import_rows.c.phase == 'preview',
                c.import_rows.c.status == 'waiting')):
            body = json.loads(payload) if payload else {}
            refs = ([(p, v) for p, _, v, _, _ in documents().ref_items(kind, body)] if kinds.is_document(kind) else
                    [(p, body[p]) for p, _ in _ref_paths(kind, body) if isinstance(body.get(p), str)])
            keys = [(p, norm_key(v)) for p, v in refs if (p, norm_key(v)) in unknown]
            answers = [choices.get(k) for k in keys]
            if any(a is not None and a['action'] == 'skip' for a in answers):
                out['left_out'] += 1
            elif all(a is not None for a in answers):
                out['answered'] += 1
            else:
                out['waiting'] += 1
    out['land'] = out['create'] + out['update'] + out['answered']
    return out


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
        phase = _phase(r)
        items.append({'id': r['id'], 'kind': r['kind'], 'mode': r['mode'], 'state': r['state'], 'source': r['source'],
                      'file_name': r['file_name'], 'rows': r['row_count'], 'batch': r['batch_id'],
                      'counts': _counts(s, r['id'], phase), 'created_at': r['created_at'],
                      'created_by': r['created_by'], 'reason': r['reason']})
    return {'items': items, 'next_cursor': rows[-1]['id'] if more and rows else None}


def rows(s, ctx, inp) -> dict:
    run = _run_read(s, inp.run)
    phase = inp.phase or _phase(run)
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
    run = _run_read(s, run_id)
    mapping = json.loads(run['mapping'])
    phase = _phase(run)
    refused = s.company.conn.execute(sa.select(c.import_rows.c.row_no, c.import_rows.c.code, c.import_rows.c.message,
                                               c.import_rows.c.line).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == phase,
        sa.or_(c.import_rows.c.status == 'refused', c.import_rows.c.code == 'skipped_by_decision'))
        .order_by(c.import_rows.c.row_no)).all()
    cells = {r[0]: (json.loads(r[1] or '[]'), json.loads(r[2]) if r[2] else None) for r in s.company.conn.execute(
        sa.select(c.import_rows.c.row_no, c.import_rows.c.cells, c.import_rows.c.spans).where(
        c.import_rows.c.run_id == run_id, c.import_rows.c.phase == 'preview',
        c.import_rows.c.row_no.in_([r[0] for r in refused])))} if refused else {}
    headers = list(mapping.get('headers') or [])
    body = []
    for row_no, code, message, line in refused:
        # a document's every row, each with its own line, so the corrected file imports the whole document again
        for own, own_line in documents().error_rows(*cells.get(row_no, ([], None))):
            own = list(own)[:len(headers)] + [''] * (len(headers) - len(own))
            body.append(own + [code or '', message or '', str(own_line or line)])
    stem = (run['file_name'] or run['kind']).rsplit('.', 1)[0]
    return {'run': run_id, 'file_name': f'{stem} - refused rows.csv', 'rows': len(body),
            'content': tabular.write_csv(headers + ['code', 'message', 'line'], body)}


def kinds_list() -> dict:
    out = []
    for k in kinds.KINDS.values():
        specs = kinds.fields(k.name)
        required = (list(kinds.required(k.name)) if kinds.is_document(k) else
                    [p for p, s in specs.items() if s.required])
        out.append({'kind': k.name, 'command': k.create, 'update_command': k.update, 'summary': k.summary,
                    'required': required, 'key': list(k.key_default),
                    'references': {p: s.target for p, s in specs.items() if s.type == 'reference'},
                    'example_header': template(k.name)['header']})
    return {'kinds': out}


_EXAMPLES = {'string': 'Example', 'money': '125.00', 'date': '2026-01-31', 'boolean': 'yes', 'integer': '30',
             'decimal': '2', 'reference': '', 'any': ''}


# Example rows that preview clean in a company on the general chart (a sales rep's employee imported first).
_EXAMPLE_ROWS = {
    'customer': {'name': 'Acme Plumbing'}, 'vendor': {'name': 'Northside Supply'},
    'item': {'name': 'Service call', 'type': 'service', 'description': 'Service call', 'price': '125.00',
             'cost': '', 'income_account_id': 'Service Income', 'expense_account_id': ''},
    'account': {'number': '6997', 'name': 'Trade Show Supplies', 'type': 'expense',
                'description': 'Booths and banners'},
    'term': {'name': 'Net 45', 'kind': 'standard', 'due_days': '45'},
    'price-level': {'name': 'Wholesale', 'kind': 'fixed_percent', 'percent': '-10'},
    'sales-rep': {'name': 'Pat Lee', 'initials': 'PL', 'name_type': 'employee', 'name_id': 'Pat Lee'},
}
_TEMPLATE_PATHS = {
    'customer': ['name', 'company_name', 'email', 'phone', 'terms_id', 'billing_address.line1',
                 'billing_address.city', 'billing_address.state', 'billing_address.postal_code'],
    'vendor': ['name', 'company_name', 'email', 'phone', 'terms_id', 'address.line1', 'address.city',
               'address.state', 'address.postal_code'],
    'item': ['name', 'type', 'description', 'price', 'cost', 'income_account_id', 'expense_account_id'],
    'account': ['number', 'name', 'type', 'description'],
    'term': ['name', 'kind', 'due_days'],
    'price-level': ['name', 'kind', 'percent'],
    'invoice': ['number', 'customer', 'date', 'lines[].item', 'lines[].description', 'lines[].quantity',
                'lines[].unit_price'],
    'bill': ['vendor', 'supplier_reference', 'date', 'expenses[].account', 'expenses[].amount', 'expenses[].memo'],
    'payment': ['customer', 'date', 'amount', 'reference', 'payment_method', 'deposit_to', 'applications[].invoice',
                'applications[].amount'],
    'bill-payment': ['date', 'funding_account', 'method', 'check_number', 'bills[].bill', 'bills[].amount'],
    'journal': ['number', 'date', 'lines[].account', 'lines[].side', 'lines[].amount', 'lines[].description'],
}
_EXAMPLE_ROWS.update({
    'invoice': {'number': 'INV-1001', 'customer': 'Acme Plumbing', 'lines[].item': 'Service call',
                'lines[].description': 'Service call', 'lines[].quantity': '1', 'lines[].unit_price': '125.00'},
    'bill': {'vendor': 'Northside Supply', 'supplier_reference': 'NS-4471', 'expenses[].account': 'Utilities',
             'expenses[].amount': '84.20', 'expenses[].memo': 'March'},
    'payment': {'customer': 'Acme Plumbing', 'amount': '125.00', 'reference': '1042', 'payment_method': 'Check',
                'deposit_to': 'Undeposited Funds', 'applications[].invoice': 'INV-1001',
                'applications[].amount': '125.00'},
    'bill-payment': {'funding_account': 'Checking', 'method': 'check', 'check_number': '2201',
                     'bills[].bill': 'B-1001', 'bills[].amount': '84.20'},
    'journal': {'number': 'JE-1', 'lines[].account': 'Checking', 'lines[].side': 'debit', 'lines[].amount': '10.00',
                'lines[].description': 'Correction'},
})


def template(kind_name: str) -> dict:
    k = kinds.kind(kind_name)
    specs = kinds.fields(kind_name)
    common = _TEMPLATE_PATHS.get(k.name)
    if common is None and kinds.is_document(k):
        common = list(dict.fromkeys([*k.key_default, *(p for p, f in specs.items() if f.required)]))
    paths = common or [p for p, s in specs.items() if s.required or p in ('name',)]
    paths = [p for p in dict.fromkeys(([] if common else [p for p, s in specs.items() if s.required]) + paths)
             if p in specs]
    given = _EXAMPLE_ROWS.get(k.name, {})
    example = []
    for p in paths:
        spec = specs[p]
        if p in given:
            example.append(given[p])
        elif spec.type == 'enum':
            example.append(spec.enum[0])
        else:
            example.append(_EXAMPLES.get(spec.type, ''))
    return {'kind': k.name, 'command': k.create, 'header': paths, 'example': example,
            'content': tabular.write_csv(paths, [example]),
            'fields': [{'path': p, 'type': s.type, 'required': s.required, 'enum': list(s.enum) or None,
                        'reference_to': s.target} for p, s in specs.items()]}
