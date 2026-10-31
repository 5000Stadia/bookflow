"""Grid edits: `bulk-edit` (R107 increment 6, design/bulk-import.md §7, §8a).

A grid edit is a selection of one list (customers and jobs, vendors, items, accounts: the list's own `query` filters,
search text or named records), cell edits, a pasted block and column operations ("price up 5 percent, round to
0.05"). Operations expand to concrete values before anything is stored, so the diff and the audit hold values, never
formulas.

- **Preview** reads each record as its `show` gives it, works out every field's value before and after, and dry-runs
  the record's own `update` with the version it read, so a bad value, an inactive reference or a taken name refuses
  that record by the command's own code. It writes only ``bulk_edit_runs`` and ``bulk_edit_rows``.
- **Apply** sends exactly that diff, one ordinary `update` per record carrying its previewed version: an edit made
  meanwhile to other fields merges (``merged_over_versions``), one to the same field refuses that record
  (`E_VERSION_CONFLICT`, naming who and when) and the rest go on, unless the edit is all or nothing. Each update is its
  own audit event with source reference ``bulk-edit:<run>``, and records its batch effect (the prior values, read just
  before it) in its own transaction.
- **Reverse** is the batch ledger's: every record gets its prior values back through its update, as one unit, refused
  naming each record edited again since; ``skip_changed`` reverses the rest (``reversed_partly``).

Money is exact: values are Decimals (never floats) and totals Python integers; a computed value rounds half-up to the
currency's places (or the step asked for).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from functools import lru_cache
from typing import Any

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, import_kinds, imports
from bookflow.company import schema as c
from bookflow.company.bulk_edit_schema import KINDS
from bookflow.core.ids import new_id

OWNER = batches.OWNER
KIND = 'bulk-edit'
MAX_RECORDS = 5000
ROWS_SHOWN = 200
GRID_PAGE = 50  # records a grid shows at once by default; the selection itself may hold up to MAX_RECORDS
SOURCE = 'bulk-edit'
DEFAULT_COLUMNS = {'customer': ('terms_id', 'credit_limit', 'email', 'phone'),
                   'vendor': ('terms_id', 'account_number', 'email', 'phone'),
                   'item': ('price', 'cost', 'description'),
                   'account': ('number', 'description')}
NEVER = {'account': {'type'}}  # §7: an account's type is never a grid column
NUMERIC = ('money', 'decimal', 'integer')
TEXT = ('string', 'custom')

batches.register(batches.Adapter(KIND, 'record', label='Grid edit'))


def _kind(name: str) -> import_kinds.Kind:
    if name not in KINDS:
        raise BookflowError('E_VALIDATION', message=f'Bulk edit works on {", ".join(KINDS)}; not {name!r}.',
                            details={'field': 'kind', 'value': name})
    return import_kinds.kind(name)


def now() -> str:
    return batches.now()


# ------------------------------------------------------------------ columns

def _label(field: str) -> str:
    text = field[:-3] if field.endswith('_id') else field
    return text.replace('_', ' ').capitalize()


@lru_cache(maxsize=None)
def _static_columns(kind_name: str) -> tuple[dict, ...]:
    """The update command's scalar inputs that its `show` also gives (so a reversal can restore each exactly)."""
    from bookflow.core import registry
    k = _kind(kind_name)
    update = registry.get(k.update).input_model
    shown = set(registry.get(f'{k.noun} show').output_model.model_fields)
    schema = update.model_json_schema()
    specs = import_kinds.fields(kind_name)
    refs = {r.field: r.target for r in k.definition.references if '|' not in r.target}
    out = []
    for name, node in schema['properties'].items():
        if name in (k.selector, 'expected_version', 'custom_fields') or name in NEVER.get(kind_name, ()) \
                or name not in shown:
            continue
        branches = import_kinds._branches(schema, node)
        branch = branches[0] if branches else {}
        if branch.get('type') in ('array', 'object'):
            continue  # nested records (addresses, contacts, members) are never grid columns
        spec = specs.get(name)
        if spec is not None:
            type_, target, enum, max_length = spec.type, spec.target, spec.enum, spec.max_length
        else:
            target = refs.get(name) or ('account' if name.endswith('_account_id') else None)
            enum = tuple(branch.get('enum', ()))
            type_ = ('money' if name in k.money else 'date' if name in k.dates else 'decimal' if name in k.decimals
                     else 'reference' if target else 'enum' if enum else branch.get('type') or 'any')
            max_length = branch.get('maxLength')
        if type_ == 'any':
            type_ = 'string'
        out.append({'field': name, 'label': _label(name), 'type': type_, 'choices': list(enum), 'target': target,
                    'max_length': max_length, 'default': name in DEFAULT_COLUMNS[kind_name]})
    return tuple(out)


def columns(s, kind_name: str) -> list[dict]:
    """Every editable column of the kind: the static ones, then `custom:<name>` for each custom field it carries."""
    from bookflow.company import custom_fields
    k = _kind(kind_name)
    out = [dict(col) for col in _static_columns(kind_name)]
    record_type = k.definition.record_type
    if record_type in custom_fields.SUPPORTED_VALUE_SCOPES:
        for d in custom_fields._applicable_definitions(s.company.conn, record_type):
            if d.get('active', True):
                out.append({'field': f"custom:{d['name']}", 'label': d['name'], 'type': 'custom', 'choices': [],
                            'target': None, 'max_length': None, 'default': False, 'definition_id': d['id']})
    return out


def _column_map(s, kind_name: str) -> dict[str, dict]:
    cols = columns(s, kind_name)
    out = {col['field']: col for col in cols}
    for col in cols:  # a label or a casefolded name finds its column too (a pasted header)
        out.setdefault(col['label'].casefold(), col)
        out.setdefault(col['field'].casefold(), col)
    return out


def _column(cmap: dict, field: str) -> dict:
    col = cmap.get(field) or cmap.get(field.casefold())
    if col is None:
        raise BookflowError('E_VALIDATION', message=(f'{field!r} is not a column this edit can change; '
                                                     '`bulk-edit columns` lists them.'),
                            details={'field': 'field', 'value': field})
    return col


# ------------------------------------------------------------------ reading records

def _books(s, k) -> imports.Books:
    return imports.Books(s, k, SOURCE)


_EQUALS = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.+?)\s*$')
_ULID = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')


def _named_filters(s, sel, books) -> list[str]:
    """A reference filter given by name (`category_id=Fixtures`) is read as the record it names, as a column's value is
    (R107 closing trial: the name matched nothing, silently); a name that names nothing is refused."""
    out, cmap = [], None
    for text in sel.filter:
        match = _EQUALS.match(str(text))
        if match and match.group(1).endswith('_id') and not _ULID.match(match.group(2)):
            cmap = cmap if cmap is not None else _column_map(s, sel.kind)
            col = cmap.get(match.group(1))
            if col is not None and col['type'] == 'reference' and col['target']:
                found = books.resolve(col['target'], match.group(2))
                if found is None:
                    raise BookflowError('E_RECORD_NOT_FOUND', message=(
                        f"No {col['target']} {match.group(2)!r} for the filter {text!r}."),
                        details={'field': 'filter', 'record_type': col['target'], 'selector': match.group(2)})
                text = f'{match.group(1)}={found}'
        out.append(text)
    return out


def select(s, ctx, k, sel, books, *, limit: int = MAX_RECORDS) -> tuple[list[str], int]:
    """The records chosen, in order: named ones, else the list's own `query` (its filters and search), paged."""
    if sel.records:
        ids, missing = [], []
        for text in sel.records:
            found = books.resolve(k.noun, str(text))
            (ids.append(found) if found else missing.append(text))
        if missing:
            raise BookflowError('E_RECORD_NOT_FOUND', message=(
                f"No {k.noun} {', '.join(repr(m) for m in missing[:5])}{' ...' if len(missing) > 5 else ''}."),
                details={'record_type': k.noun, 'selector': missing[0], 'missing': missing[:50]})
        ids = list(dict.fromkeys(ids))
        return ids[:limit], len(ids)
    ids, cursor, total = [], None, None
    filters = _named_filters(s, sel, books)
    while True:
        payload = {'filter': filters, 'include_inactive': sel.include_inactive, 'projection': 'reference',
                   'limit': 200}
        if sel.query:
            payload['query'] = sel.query
        if cursor:
            payload['cursor'] = cursor
        out = batches.run_nested(s, ctx, f'{k.noun} query', payload, source_ref='bulk-edit:select', recovery=False)
        total = out.get('matching_total', total)
        ids += [row['id'] for row in out['items']]
        cursor = out.get('next_cursor')
        if not cursor or len(ids) > limit:
            break
    total = total if isinstance(total, int) else len(ids)
    return ids[:limit], max(total, len(ids))


def _dumped(value) -> dict:
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else json.loads(json.dumps(value, default=str))


def shown(s, ctx, k, ids: list[str]) -> dict[str, dict]:
    """Each record as its `show` gives it. The caller's read permission is asked once (batches.require_read); the show
    planner then runs in this session per record, writing nothing."""
    from bookflow.core import registry
    cmd = registry.get(f'{k.noun} show')
    out = {}
    for rid in ids:
        inp = cmd.input_model.model_validate({k.selector: rid})
        out[rid] = _dumped(cmd.plan(inp, ctx, s).preview)
    return out


def _label_of(record: dict) -> str:
    return str(record.get('full_name') or record.get('name') or record.get('id'))


def current_value(col: dict, record: dict) -> Any:
    """A field's value as the update takes it (money as its amount text, a custom field by its definition)."""
    if col['type'] == 'custom':
        for entry in record.get('custom_fields') or []:
            if entry.get('definition_id') == col['definition_id']:
                return entry.get('value')
        return None
    return imports._plain(record.get(col['field']))


class Names:
    """Reference ids shown by name, and typed names resolved to ids, from one read per list."""

    def __init__(self, books: imports.Books):
        self.books = books
        self._by_id: dict[str, dict[str, str]] = {}

    def name(self, target: str, record_id: str | None) -> str:
        if record_id is None:
            return ''
        if target not in self._by_id:
            index = self.books.ref_index(target) if self.books.readable(target) else {'order': [], 'names': []}
            self._by_id[target] = dict(zip(index['order'], index['names']))
        return str(self._by_id[target].get(record_id, record_id))

    def resolve(self, target: str, text: str) -> str | None:
        return self.books.resolve(target, text)


def display(col: dict, value: Any, names: Names) -> str:
    if value is None or value == '':
        return ''
    if col['type'] == 'reference' and col['target']:
        return names.name(col['target'], value)
    if col['type'] == 'boolean':
        return 'Yes' if value else 'No'
    return str(value)


def selection_fingerprint(kind_name: str, ids: list[str]) -> str:
    """The selection's records in order: a paste without a header fills them by position, so it must land on the
    records the grid showed (a record added, removed or renamed into another place since moves every row after it)."""
    return hashlib.sha256('\n'.join([kind_name, *ids]).encode()).hexdigest()


def grid(s, ctx, inp) -> dict:
    k = _kind(inp.kind)
    batches.require_read(s, f'{k.noun} show')
    cmap = _column_map(s, inp.kind)
    wanted = inp.columns or [col['field'] for col in columns(s, inp.kind) if col['default']]
    cols = [_column(cmap, f) for f in wanted]
    books = _books(s, k)
    names = Names(books)
    every, total = select(s, ctx, k, inp, books)
    ids = every[inp.offset:inp.offset + inp.limit]
    records = shown(s, ctx, k, ids)
    out = []
    for rid in ids:
        record = records[rid]
        out.append({'id': rid, 'version': record.get('version'), 'label': _label_of(record),
                    'active': record.get('active', True),
                    'values': {col['field']: display(col, current_value(col, record), names) for col in cols}})
    return {'kind': inp.kind, 'columns': cols, 'records': out, 'total': total, 'offset': inp.offset,
            'truncated': total > inp.offset + len(ids),
            'selection_fingerprint': selection_fingerprint(inp.kind, every) if total <= MAX_RECORDS else None}


# ------------------------------------------------------------------ values

_NUMBER = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)$')
_TRUE = {'true', 'yes', 'y', '1', 'on'}
_FALSE = {'false', 'no', 'n', '0', 'off'}


def _bad(col: dict, value: Any, why: str) -> BookflowError:
    return BookflowError('E_VALIDATION', message=f"{col['label']}: {why}: {value!r}.",
                         details={'field': col['field'], 'value': value if isinstance(value, (str, int, bool))
                                  else str(value), 'problem': why})


def decimal_of(col: dict, value: Any) -> Decimal:
    if isinstance(value, bool):
        raise _bad(col, value, 'not a number')
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        raise _bad(col, value, 'give the number as text so it stays exact')
    text = str(value).strip()
    if col['type'] == 'money':
        text = text.lstrip('$').replace(',', '') if re.fullmatch(r'\$?-?[\d,]*\.?\d*', text) else text
    if not _NUMBER.match(text):
        raise _bad(col, value, 'not an amount' if col['type'] == 'money' else 'not a number')
    return Decimal(text)


def parse(col: dict, value: Any, names: Names) -> Any:
    """A typed value as the update command takes it; "" or null clears."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    t = col['type']
    if t in ('money', 'decimal'):
        return _text(decimal_of(col, value))
    if t == 'integer':
        number = decimal_of(col, value)
        if number != number.to_integral_value():
            raise _bad(col, value, 'not a whole number')
        return int(number)
    if t == 'boolean':
        if isinstance(value, bool):
            return value
        word = str(value).strip().casefold()
        if word in _TRUE | _FALSE:
            return word in _TRUE
        raise _bad(col, value, 'not yes or no')
    if t == 'enum':
        text = str(value).strip()
        for choice in col['choices']:
            if text.casefold() in (choice.casefold(), choice.replace('_', ' ').casefold()):
                return choice
        raise _bad(col, value, 'not one of ' + ', '.join(col['choices']))
    if t == 'date':
        try:
            return dt.date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            raise _bad(col, value, 'not a date (YYYY-MM-DD)') from None
    if t == 'reference':
        found = names.resolve(col['target'], str(value))
        if found is None:
            raise BookflowError('E_VALIDATION', message=(
                f"{col['label']}: no {col['target'].replace('-', ' ')} {str(value)!r}."),
                details={'field': col['field'], 'value': str(value), 'problem': 'unknown_reference',
                         'target': col['target'], 'suggestions': names.books.suggestions(col['target'], str(value))})
        return found
    if t == 'custom':
        return value
    return str(value)


def _text(number: Decimal) -> str:
    text = format(number, 'f')
    return '0' if text in ('-0', '+0') else text


def _step(col: dict, rounding: str | None, places: int) -> Decimal:
    if rounding is not None:
        try:
            step = Decimal(str(rounding).strip())
        except InvalidOperation:
            step = Decimal(0)
        if step <= 0:
            raise BookflowError('E_VALIDATION', message=f'Round to a positive step, not {rounding!r}.',
                                details={'field': 'round', 'value': rounding})
        return step
    return {'money': Decimal(1).scaleb(-places), 'integer': Decimal(1)}.get(col['type'], Decimal('0.000001'))


def rounded(number: Decimal, step: Decimal, col: dict, places: int) -> Decimal:
    """Half-up (away from zero at the half) to a multiple of ``step``, shown with the currency's places for money."""
    with localcontext() as context:
        context.prec = 60
        result = (number / step).to_integral_value(rounding=ROUND_HALF_UP) * step
        exponent = min(step.as_tuple().exponent, -places if col['type'] == 'money' else 0)
        return result.quantize(Decimal(1).scaleb(exponent))


def same(col: dict, a: Any, b: Any) -> bool:
    if a is None or b is None or a == '' or b == '':
        return (a is None or a == '') and (b is None or b == '')
    if col['type'] in ('money', 'decimal', 'integer'):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except InvalidOperation:
            return a == b
    return a == b


# ------------------------------------------------------------------ the diff

def _paste_changes(inp, k, cmap: dict, ids: list[str], labels: dict[str, str], books) -> list[dict]:
    """A pasted block as cell changes: with a header (any cell names a column), columns by header and a Name column
    picking each row's record; else rows fill the selection in order over ``paste_columns``."""
    lines = [line for line in inp.paste.replace('\r\n', '\n').replace('\r', '\n').split('\n') if line.strip()]
    if not lines:
        return []
    table = [line.split('\t') for line in lines]
    head = [cell.strip() for cell in table[0]]
    keys = {'name', 'full name', k.noun.replace('-', ' '), f'{k.noun} name'}
    named = [i for i, cell in enumerate(head) if cell.casefold() in keys]
    matched = {i: (cmap.get(cell) or cmap.get(cell.casefold())) for i, cell in enumerate(head)}
    has_header = bool(named) or any(col is not None for col in matched.values())
    out = []
    if has_header:
        fields = {i: col['field'] for i, col in matched.items() if col is not None and i not in named}
        body = table[1:]
    else:
        if not inp.records and inp.selection_fingerprint != selection_fingerprint(k.name, ids):
            raise BookflowError('E_VALIDATION', message=(
                'A paste without a header row fills the records by position, and the records the grid showed '
                'have changed since (one added, removed or renamed), so its rows would land on other records. Show '
                'the grid again and paste once more, or paste with a header row and a Name column.'
                if inp.selection_fingerprint else
                'A paste without a header row fills the records by position: give the grid\'s '
                '`selection_fingerprint` (so it is refused if the records changed since), name the `records`, or '
                'paste with a header row and a Name column.'),
                details={'field': 'selection_fingerprint', 'problem': 'selection_changed'
                         if inp.selection_fingerprint else 'required'})
        given = inp.paste_columns or [f for f in DEFAULT_COLUMNS[k.name]]
        fields = {i: _column(cmap, f)['field'] for i, f in enumerate(given)}
        body = table
    unknown = [head[i] for i, col in matched.items() if has_header and col is None and i not in named and head[i]]
    if unknown:
        raise BookflowError('E_VALIDATION', message=(f"Pasted column{'s' if len(unknown) > 1 else ''} "
                                                     f"{', '.join(repr(u) for u in unknown)} match no column."),
                            details={'field': 'paste', 'unknown': unknown})
    for n, cells in enumerate(body):
        if named:
            text = cells[named[0]].strip() if named[0] < len(cells) else ''
            record = books.resolve(k.noun, text) if text else None
            if record is None:
                raise BookflowError('E_RECORD_NOT_FOUND', message=f'Pasted row {n + 1}: no {k.noun} {text!r}.',
                                    details={'record_type': k.noun, 'selector': text, 'field': 'paste'})
        else:
            if n >= len(ids):
                raise BookflowError('E_VALIDATION', message=(
                    f'The paste has {len(body)} rows but the selection {len(ids)} records.'),
                    details={'field': 'paste', 'rows': len(body), 'records': len(ids)})
            record = ids[n]
        for i, field in fields.items():
            if i < len(cells):
                out.append({'record': record, 'field': field, 'value': cells[i].strip()})
    return out


def _describe(op) -> str:
    field = op.field
    if op.op == 'set':
        return f'{field} = {op.value!r}'
    if op.op == 'percent':
        return f"{field} {'+' if not str(op.by).startswith('-') else ''}{op.by}%" + (
            f' (round {op.round})' if op.round else '')
    if op.op in ('add', 'multiply'):
        return f"{field} {'+' if op.op == 'add' else 'x'} {op.by}" + (f' (round {op.round})' if op.round else '')
    if op.op == 'replace':
        return f'{field}: {op.find!r} to {op.replace!r}'
    if op.op == 'set_from':
        return f'{field} from {op.from_}'
    return f'{op.op.replace("_", " ")} {field}'


def _operate(op, col: dict, cmap: dict, ids: list[str], values: dict, names: Names, places: int) -> None:
    """Expand one column operation to a concrete value per selected record (in ``values``)."""
    field = col['field']
    if op.op in ('add', 'multiply', 'percent'):
        if col['type'] not in NUMERIC:
            raise BookflowError('E_VALIDATION', message=f"{col['label']} is not a number; {op.op} needs one.",
                                details={'field': 'field', 'value': field})
        if op.by is None:
            raise BookflowError('E_VALIDATION', message=f'{op.op} needs `by`.', details={'field': 'by'})
        by = decimal_of({'label': 'by', 'field': 'by', 'type': 'decimal'}, op.by)
        step = _step(col, op.round, places)
        for rid in ids:
            current = values[rid].get(field)
            if current is None or current == '':
                continue  # a blank has nothing to add to or multiply; it stays blank
            with localcontext() as context:
                context.prec = 60
                number = Decimal(str(current))
                result = (number + by if op.op == 'add' else number * by if op.op == 'multiply'
                          else number * (Decimal(100) + by) / Decimal(100))
            result = rounded(result, step, col, places)
            values[rid][field] = int(result) if col['type'] == 'integer' else _text(result)
        return
    if op.op == 'set':
        value = parse(col, op.value, names)
        for rid in ids:
            values[rid][field] = value
    elif op.op == 'clear':
        for rid in ids:
            values[rid][field] = None
    elif op.op == 'copy_down':
        if ids:
            first = values[ids[0]].get(field)
            for rid in ids[1:]:
                values[rid][field] = first
    elif op.op == 'replace':
        if col['type'] not in TEXT:
            raise BookflowError('E_VALIDATION', message=f"{col['label']} is not text; replace needs text.",
                                details={'field': 'field', 'value': field})
        if not op.find:
            raise BookflowError('E_VALIDATION', message='replace needs `find`.', details={'field': 'find'})
        for rid in ids:
            current = values[rid].get(field)
            if isinstance(current, str) and op.find in current:
                values[rid][field] = current.replace(op.find, op.replace or '') or None
    elif op.op == 'set_from':
        if not op.from_:
            raise BookflowError('E_VALIDATION', message='set_from needs `from`.', details={'field': 'from'})
        source = _column(cmap, op.from_)
        if source['type'] != col['type'] and not {source['type'], col['type']} <= set(TEXT) \
                or source.get('target') != col.get('target'):
            raise BookflowError('E_VALIDATION', message=(f"{source['label']} ({source['type']}) cannot fill "
                                                         f"{col['label']} ({col['type']})."),
                                details={'field': 'from', 'value': op.from_})
        for rid in ids:
            values[rid][field] = values[rid].get(source['field'])


def _payload(k, cols: dict[str, dict], record_id: str, version: int | None, after: dict[str, Any]) -> dict:
    body: dict[str, Any] = {k.selector: record_id}
    if version is not None:
        body['expected_version'] = version
    for field, value in after.items():
        col = cols[field]
        if col['type'] == 'custom':
            body.setdefault('custom_fields', {})[col['definition_id']] = value
        else:
            body[field] = value
    return body


def _money_units(value: Any) -> int:
    """An amount in millionths, as an exact integer (sums are Python integers, never floats or SQL)."""
    if value is None or value == '':
        return 0
    with localcontext() as context:
        context.prec = 60
        return int(Decimal(str(value)).scaleb(6).to_integral_value(rounding=ROUND_HALF_UP))


def _units_text(units: int, places: int) -> str:
    with localcontext() as context:
        context.prec = 60
        number = Decimal(units).scaleb(-6)
        exponent = min(-places, number.normalize().as_tuple().exponent if number else -places)
        return _text(number.quantize(Decimal(1).scaleb(exponent)))


def prepare_preview(s, ctx, inp) -> dict:
    """Work out the diff and dry-run each record's update; nothing is written (apply_preview stores it)."""
    k = _kind(inp.kind)
    batches.require_read(s, f'{k.noun} show')
    if not (inp.changes or inp.paste or inp.operations):
        raise BookflowError('E_VALIDATION', message='Say what to change: `changes`, `paste` or `operations`.',
                            details={'field': 'operations'})
    cmap = _column_map(s, inp.kind)
    books = _books(s, k)
    names = Names(books)
    places = imports._places(s)
    ids, total = select(s, ctx, k, inp, books)
    if total > MAX_RECORDS:
        raise BookflowError('E_VALIDATION', message=(f'The selection holds {total} {k.noun}s; a grid edit takes at '
                                                     f'most {MAX_RECORDS}. Narrow the filter.'),
                            details={'field': 'filter', 'total': total, 'limit': MAX_RECORDS})
    records = shown(s, ctx, k, ids)
    labels = {rid: _label_of(records[rid]) for rid in ids}
    touched: dict[str, dict] = {}
    changes = [ch.model_dump() for ch in inp.changes]
    if inp.paste:
        changes += _paste_changes(inp, k, cmap, ids, labels, books)
    for ch in changes:
        touched[_column(cmap, ch['field'])['field']] = _column(cmap, ch['field'])
    for op in inp.operations:
        col = _column(cmap, op.field)
        touched[col['field']] = col
        if op.op == 'set_from' and op.from_:
            source = _column(cmap, op.from_)
            touched.setdefault(source['field'], source)
    values = {rid: {f: current_value(col, records[rid]) for f, col in touched.items()} for rid in ids}
    for ch in changes:
        col = _column(cmap, ch['field'])
        record = ch['record']
        rid = record if record in values else books.resolve(k.noun, str(record))
        if rid is None or rid not in values:
            raise BookflowError('E_RECORD_NOT_FOUND', message=f'{k.noun} {record!r} is not in the selection.',
                                details={'record_type': k.noun, 'selector': str(record), 'field': 'changes'})
        values[rid][col['field']] = parse(col, ch['value'], names)
    for op in inp.operations:
        _operate(op, _column(cmap, op.field), cmap, ids, values, names, places)
    rows, unchanged = [], 0
    money = [f for f, col in touched.items() if col['type'] == 'money']
    sums = {f: [0, 0] for f in money}
    for rid in ids:
        record = records[rid]
        diff = []
        for field, col in touched.items():
            before, after = current_value(col, record), values[rid].get(field)
            if not same(col, before, after):
                diff.append({'field': field, 'before': before, 'after': after,
                             'before_text': display(col, before, names), 'after_text': display(col, after, names)})
        if not diff:
            unchanged += 1
            continue
        body = _payload(k, touched, rid, record.get('version'), {d['field']: d['after'] for d in diff})
        status, code, message = 'change', None, None
        try:
            batches.run_nested(s, ctx, k.update, body, source_ref='bulk-edit:preview', dry_run=True, recovery=False)
        except BookflowError as error:
            status, code, message = 'refused', error.code, imports._message(error)
        if status == 'change':
            for d in diff:
                if d['field'] in sums:
                    sums[d['field']][0] += _money_units(d['before'])
                    sums[d['field']][1] += _money_units(d['after'])
        rows.append({'seq': len(rows) + 1, 'record_type': k.definition.record_type, 'record_id': rid,
                     'label': labels[rid], 'version': record.get('version'), 'status': status, 'changes': diff,
                     'code': code, 'message': message})
    if inp.atomic and any(r['status'] == 'refused' for r in rows):
        first = next(r for r in rows if r['status'] == 'refused')
        raise BookflowError('E_VALIDATION', message=(
            f"All or nothing: {sum(r['status'] == 'refused' for r in rows)} record(s) would be refused, first "
            f"{first['label']}: {first['message']}"), details={'field': 'atomic', 'refused': [
                {'record': r['label'], 'code': r['code'], 'message': r['message']}
                for r in rows if r['status'] == 'refused'][:50]})
    told = '; '.join([*(_describe(op) for op in inp.operations),
                      *([f'{len(changes)} cell edit(s)'] if changes else [])])
    label = f"{k.noun.capitalize()} grid edit ({len(ids)}): {told}"[:200]
    return {'run_id': new_id(), 'kind': k, 'input': inp, 'rows': rows, 'unchanged': unchanged, 'selected': len(ids),
            'label': label, 'totals': {f: {'before': _units_text(a, places), 'after': _units_text(b, places),
                                           'change': _units_text(b - a, places)} for f, (a, b) in sums.items()},
            'selection': {'filter': list(inp.filter), 'query': inp.query, 'include_inactive': inp.include_inactive,
                          'records': inp.records},
            'operations': {'changes': changes, 'operations': [op.model_dump(by_alias=True, exclude_none=True)
                                                              for op in inp.operations],
                           'paste': bool(inp.paste)}}


def apply_preview(s, ctx, prepared: dict) -> None:
    """Store the diff, inside the preview command's own transaction."""
    at = now()
    k, inp = prepared['kind'], prepared['input']
    s.company.conn.execute(sa.insert(c.bulk_edit_runs).values(
        id=prepared['run_id'], kind=k.name, state='previewed', atomic=inp.atomic,
        selection=json.dumps(prepared['selection'], sort_keys=True),
        operations=json.dumps(prepared['operations'], sort_keys=True, default=str), label=prepared['label'],
        record_count=prepared['selected'], unchanged_count=prepared['unchanged'], batch_id=None, reason=ctx.reason,
        version=1, created_at=at, created_by=s.actor.id, created_via=str(getattr(ctx.interface, 'value',
                                                                                  ctx.interface))[:16],
        updated_at=at))
    if prepared['rows']:
        s.company.conn.execute(sa.insert(c.bulk_edit_rows), [{
            'run_id': prepared['run_id'], 'seq': r['seq'], 'record_type': r['record_type'],
            'record_id': r['record_id'], 'label': r['label'][:1004], 'version': r['version'], 'status': r['status'],
            'changes': json.dumps(r['changes'], sort_keys=True, default=str), 'code': r['code'],
            'message': r['message']} for r in prepared['rows']])


# ------------------------------------------------------------------ apply

def _run_row(s, run_id: str) -> dict:
    row = s.company.conn.execute(sa.select(c.bulk_edit_runs).where(c.bulk_edit_runs.c.id == run_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'bulk_edit_run', 'selector': run_id})
    return dict(row)


def run_read(s, run_id: str) -> dict:
    """The run, if the caller may read its list: its rows are those records' values."""
    run = _run_row(s, run_id)
    batches.require_read(s, f"{_kind(run['kind']).noun} show")
    return run


def _tx(s, work) -> Any:
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            out = work()
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return out


def _set_state(s, run: dict, state: str) -> None:
    _tx(s, lambda: s.company.conn.execute(sa.update(c.bulk_edit_runs).where(c.bulk_edit_runs.c.id == run['id']).values(
        state=state, version=c.bulk_edit_runs.c.version + 1, updated_at=now())))
    run['state'] = state


def _start(s, ctx, run: dict) -> None:
    """Open the edit's batch and mark it `applying` in one transaction; refused if another apply began meanwhile."""
    def work():
        batch_id = batches._insert_batch(s, ctx, kind=KIND, label=run['label'], scope=f"bulk-edit:{run['kind']}",
                                         ordering='record', source_type='bulk_edit_run', source_id=run['id'],
                                         reason=ctx.reason or run['reason'])
        changed = s.company.conn.execute(sa.update(c.bulk_edit_runs).where(
            c.bulk_edit_runs.c.id == run['id'], c.bulk_edit_runs.c.state == 'previewed').values(
            batch_id=batch_id, state='applying', version=c.bulk_edit_runs.c.version + 1, updated_at=now()))
        if changed.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', message=f"Grid edit {run['id']} was applied meanwhile.",
                                details={'field': 'run'})
        return batch_id
    run['batch_id'] = _tx(s, work)
    run['state'] = 'applying'


def _outcome(s, run_id: str, seq: int, **values) -> None:
    s.company.conn.execute(sa.update(c.bulk_edit_rows).where(
        c.bulk_edit_rows.c.run_id == run_id, c.bulk_edit_rows.c.seq == seq).values(applied_at=now(), **values))


def prior_values(k, cols: dict[str, dict], current: dict, payload: dict, after: dict | None) -> dict:
    """What the reversal sends back: each field's value just before this update, input-shaped (imports.before_values
    for the update's own fields; a custom field by its definition)."""
    plain = {key: value for key, value in payload.items() if key != 'custom_fields'}
    without = {key: value for key, value in current.items() if key != 'custom_fields'}
    later = None if after is None else {key: value for key, value in after.items() if key != 'custom_fields'}
    out = imports.before_values(k, without, plain, later)
    custom = payload.get('custom_fields') or {}
    if custom:
        by_def = {col['definition_id']: col for col in cols.values() if col['type'] == 'custom'}
        out['custom_fields'] = {def_id: current_value(by_def[def_id], current) for def_id in custom}
    return out


def _recorder(run: dict, row: dict, k, cols: dict, current: dict, payload: dict):
    def record(s, cmd, applied, output):
        out = output if isinstance(output, dict) else {}
        version = out.get('version')
        merged = list(out.get('merged_over_versions') or [])
        event_id = batches.last_event_id(s)
        batches.record_effect(s, run['batch_id'], record_type=row['record_type'], record_id=row['record_id'],
                              action='updated', version_after=version,
                              before=prior_values(k, cols, current, payload, out), event_id=event_id)
        _outcome(s, run['id'], row['seq'], outcome='merged' if merged else 'updated', version_after=version,
                 merged_over=json.dumps(merged) if merged else None, event_id=event_id,
                 outcome_message=(f'merged over version{"s" if len(merged) > 1 else ""} '
                                  f'{", ".join(map(str, merged))}: those changes touched other fields')
                 if merged else None)
    return record


def _batch_state(s, run: dict) -> str | None:
    if not run['batch_id']:
        return None
    return s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == run['batch_id'])).scalar()


def _not_run(s, run: dict) -> int:
    """Close the rows still waiting (``not_run``), inside the caller's transaction; returns how many."""
    return s.company.conn.execute(sa.update(c.bulk_edit_rows).where(
        c.bulk_edit_rows.c.run_id == run['id'], c.bulk_edit_rows.c.outcome.is_(None),
        c.bulk_edit_rows.c.status == 'change').values(outcome='not_run', applied_at=now())).rowcount


def _pending(s, run_id: str) -> int:
    return s.company.conn.execute(sa.select(sa.func.count()).select_from(c.bulk_edit_rows).where(
        c.bulk_edit_rows.c.run_id == run_id, c.bulk_edit_rows.c.status == 'change',
        c.bulk_edit_rows.c.outcome.is_(None))).scalar()


def apply(s, ctx, run_id: str, *, limit: int | None = None, interrupt=None) -> dict:
    """Apply the diff, or its next ``limit`` records (a slice: the run stays `applying` and the same call goes on)."""
    run = run_read(s, run_id)
    k = _kind(run['kind'])
    if run['state'] not in ('previewed', 'applying'):
        raise BookflowError('E_VALIDATION', message=f"Grid edit {run_id} is already {run['state']}.",
                            details={'field': 'run', 'state': run['state']})
    batch_state = _batch_state(s, run)
    if run['state'] == 'applying' and batch_state != 'live':
        # Its batch was reversed (or is being) meanwhile, so nothing more may land in it: a write recorded there
        # could never be undone with the rest. What is still waiting is left, and the edit ends cancelled.
        left = _tx(s, lambda: _not_run(s, run))
        _set_state(s, run, 'cancelled')
        raise BookflowError('E_VALIDATION', message=(
            f"Grid edit {run_id} cannot go on: its batch is {batch_state}, so nothing more may be added to it. The "
            f"{left} record(s) not yet changed were left as they are, and the edit is cancelled."),
            details={'field': 'run', 'state': 'cancelled', 'batch_state': batch_state, 'not_run': left})
    rows = [dict(r) for r in s.company.conn.execute(sa.select(c.bulk_edit_rows).where(
        c.bulk_edit_rows.c.run_id == run_id).order_by(c.bulk_edit_rows.c.seq)).mappings()]
    if run['atomic'] and any(r['status'] == 'refused' for r in rows):
        raise BookflowError('E_VALIDATION', message='All or nothing, and the preview refused records; preview again.',
                            details={'field': 'run'})
    if not any(r['status'] == 'change' for r in rows):
        raise BookflowError('E_VALIDATION', message=f'Grid edit {run_id} changes nothing.', details={'field': 'run'})
    if run['state'] == 'previewed':
        _start(s, ctx, run)
    cols = {col['field']: col for col in columns(s, run['kind'])}
    done = 0
    for row in rows:
        if row['status'] != 'change' or row['outcome'] is not None:
            continue
        if limit is not None and done >= limit:
            return show(s, ctx, run_id)  # a slice: the rest waits for the next call
        diff = json.loads(row['changes'])
        payload = _payload(k, cols, row['record_id'], row['version'], {d['field']: d['after'] for d in diff})
        current = shown(s, ctx, k, [row['record_id']])[row['record_id']]
        try:
            batches.run_nested(s, ctx, k.update, payload, source_ref=f"bulk-edit:{run_id}",
                               recorder=_recorder(run, row, k, cols, current, payload), recovery=False)
        except BookflowError as error:
            if (error.details or {}).get('problem') == 'batch_not_live':
                raise
            outcome = 'conflict' if error.code == 'E_VERSION_CONFLICT' else 'refused'
            _tx(s, lambda: _outcome(s, run_id, row['seq'], outcome=outcome, outcome_code=error.code,
                                    outcome_message=imports._message(error)))
            if run['atomic']:
                return _roll_back(s, ctx, run, row, error)
        done += 1
        if interrupt is not None:
            interrupt(done)
    outcomes = {r[0] for r in s.company.conn.execute(sa.select(c.bulk_edit_rows.c.outcome).where(
        c.bulk_edit_rows.c.run_id == run_id, c.bulk_edit_rows.c.status == 'change'))}
    refused_any = bool(outcomes & {'conflict', 'refused'}) or any(r['status'] == 'refused' for r in rows)
    _set_state(s, run, 'applied_partly' if refused_any else 'applied')
    return show(s, ctx, run_id)


def _put_back(s, ctx, run: dict, *, note: str, why: str, state: str) -> dict:
    """Stop the apply: the rows still waiting become ``not_run`` and the records already changed get their prior
    values back through the batch; the run ends ``state``, or ``failed`` (its batch still live, so `bulk-edit reverse`
    can finish the undo) if that reversal is refused."""
    _tx(s, lambda: _not_run(s, run))
    made = batches.counts(s, run['batch_id'])
    undo = ctx.model_copy(update={'reason': ctx.reason or run.get('reason') or f"Grid edit {run['id']} undone"})
    try:
        if made and _batch_state(s, run) in ('live', 'reversing', 'blocked'):
            batches.reverse(s, undo, run['batch_id'], reason=f"{why}: {note}"[:500], limit=10 ** 9)
    except BookflowError as failed:
        _set_state(s, run, 'failed')
        out = show(s, ctx, run['id'])
        out['notes'] = [f"{note}; undoing the {sum(made.values())} record(s) already changed was refused too: "
                        f"{failed.message} `bulk-edit reverse {run['id']}` finishes it once that is cleared."]
        return out
    _set_state(s, run, state)
    out = show(s, ctx, run['id'])
    out['notes'] = [f"{note}; the {sum(made.values())} record(s) already changed were put back."]
    return out


def _roll_back(s, ctx, run: dict, row: dict, error: BookflowError) -> dict:
    """All or nothing met a refusal: the records already changed get their prior values back through the batch."""
    note = f"{row['label']} was refused ({error.code}: {imports._message(error)}); all or nothing"
    return _put_back(s, ctx, run, note=note, why='All-or-nothing grid edit stopped', state='rolled_back')


def cancel(s, ctx, run_id: str) -> dict:
    """Stop an unfinished apply: what it changed so far is put back as one unit, and the rest never runs."""
    run = run_read(s, run_id)
    if run['state'] != 'applying':
        raise BookflowError('E_VALIDATION', message=(
            f"Only an unfinished apply can be cancelled; grid edit {run_id} is {run['state']}."
            + (f" `bulk-edit reverse {run_id}` undoes it." if run['state'] in ('applied', 'applied_partly') else '')),
            details={'field': 'run', 'state': run['state']})
    done = s.company.conn.execute(sa.select(sa.func.count()).select_from(c.bulk_edit_rows).where(
        c.bulk_edit_rows.c.run_id == run_id, c.bulk_edit_rows.c.outcome.isnot(None))).scalar()
    return _put_back(s, ctx, run, note=f"Cancelled after {done} record(s)", why='Grid edit cancelled',
                     state='cancelled')


# ------------------------------------------------------------------ reverse



def reverse(s, ctx, inp, *, dry_run: bool) -> dict:
    run = run_read(s, inp.run)
    if run['batch_id'] is None:
        raise BookflowError('E_VALIDATION', message=f'Grid edit {inp.run} was never applied; nothing to reverse.',
                            details={'field': 'run', 'state': run['state']})
    if run['state'] == 'applying':
        # Undoing a part of it would leave the rest to land afterwards, changed and beyond any undo.
        left = _pending(s, inp.run)
        raise BookflowError('E_VALIDATION', message=(
            f"Grid edit {inp.run} is still applying ({left} record(s) not yet changed). Finish it "
            f"(`bulk-edit apply {inp.run}`) or cancel it (`bulk-edit apply {inp.run} --cancel`, which puts back "
            "what already changed) first; then it is undone as one unit."),
            details={'field': 'run', 'state': 'applying', 'pending': left})
    reversed_now, fingerprint, skipped, skipped_total = 0, inp.fingerprint, [], 0
    while True:
        try:
            out = batches.reverse(s, ctx, run['batch_id'], reason=ctx.reason, dry_run=dry_run,
                                  limit=batches.DEFAULT_SLICE, fingerprint=fingerprint,
                                  skip_changed=inp.skip_changed)
        except BookflowError as error:
            if error.code == 'E_BATCH_BLOCKED':
                _readable(s, run, error.details.get('blockers') or [])
                first = (error.details.get('blockers') or [{}])[0]
                if first.get('person'):
                    total = error.details.get('total') or len(error.details['blockers'])
                    error.message = (f"{total} record(s) changed since this edit, first "
                                     f"{first['person']} Nothing was changed; skip_changed reverses the rest."
                                     if first.get('code') in batches.CHANGED else error.message)
                    error.args = (f'{error.code}: {error.message}',)
            raise
        reversed_now += out['reversed_now']
        if out.get('skipped'):  # every slice leaves the same records; the first reports them
            skipped, skipped_total = out['skipped'], out.get('skipped_total', len(out['skipped']))
        fingerprint = None
        if dry_run or out['state'] != 'reversing':
            break
    _readable(s, run, out.get('blockers') or [])
    _readable(s, run, skipped)
    out.update(reversed_now=reversed_now, skipped=skipped, skipped_total=skipped_total, run=inp.run)
    return out


def _readable(s, run: dict, blockers: list[dict]) -> None:
    """Each blocker as a person reads it (`person`): the record's name, who changed it (by name) and when."""
    from bookflow.company.info import principal_names
    if not blockers:
        return
    labels = dict(s.company.conn.execute(sa.select(c.bulk_edit_rows.c.record_id, c.bulk_edit_rows.c.label).where(
        c.bulk_edit_rows.c.run_id == run['id'])).all())
    names = principal_names(s.company, {b.get('changed_by') for b in blockers if b.get('changed_by')})
    for b in blockers:
        label = labels.get(b.get('record_id'), b.get('record_id'))
        b['label'] = label
        who = names.get(b.get('changed_by')) or 'someone'
        when = (b.get('changed_at') or '')[:16].replace('T', ' ')
        if b.get('code') == 'edited_again':
            b['person'] = f"{label} was changed since this edit by {who}{' at ' + when + ' UTC' if when else ''}."
        elif b.get('code') == 'later_batch':
            later = b.get('message', '').split(', ', 1)[-1].rstrip('.')
            b['person'] = f"{label} was changed since by a later batch, {later}: {b.get('clears')}."
        else:
            b['person'] = b.get('message')


# ------------------------------------------------------------------ reading

def _row_out(r: dict) -> dict:
    return {'seq': r['seq'], 'record_type': r['record_type'], 'record_id': r['record_id'], 'label': r['label'],
            'version': r['version'], 'status': r['status'], 'changes': json.loads(r['changes']), 'code': r['code'],
            'message': r['message'], 'outcome': r['outcome'], 'outcome_code': r['outcome_code'],
            'outcome_message': r['outcome_message'], 'version_after': r['version_after'],
            'merged_over': json.loads(r['merged_over']) if r['merged_over'] else [], 'event_id': r['event_id']}


def _totals(s, run: dict, rows: list[dict]) -> dict:
    places = imports._places(s)
    cols = {col['field']: col for col in _static_columns(run['kind'])}
    sums: dict[str, list[int]] = {}
    for r in rows:
        if r['status'] != 'change' or r['outcome'] in ('conflict', 'refused', 'not_run'):
            continue
        for d in json.loads(r['changes']):
            if cols.get(d['field'], {}).get('type') == 'money':
                pair = sums.setdefault(d['field'], [0, 0])
                pair[0] += _money_units(d['before'])
                pair[1] += _money_units(d['after'])
    return {f: {'before': _units_text(a, places), 'after': _units_text(b, places), 'change': _units_text(b - a, places)}
            for f, (a, b) in sums.items()}


def _skipped(s, run: dict, batch: dict) -> list[dict]:
    """After a partial reversal: the applied records the reversal left (edited again since)."""
    if batch.get('state') != 'reversed_partly' or not batch.get('reversed_by'):
        return []
    restored = set(s.company.conn.execute(sa.select(c.batch_effects.c.record_id).where(
        c.batch_effects.c.batch_id == batch['reversed_by'])).scalars())
    rows = s.company.conn.execute(sa.select(c.bulk_edit_rows.c.record_id, c.bulk_edit_rows.c.label).where(
        c.bulk_edit_rows.c.run_id == run['id'], c.bulk_edit_rows.c.outcome.in_(('updated', 'merged')))).all()
    return [{'record_id': rid, 'label': label, 'reason': 'edited again since the grid edit'}
            for rid, label in rows if rid not in restored]


def show(s, ctx, run_id: str, *, outcome: str | None = None, limit: int = ROWS_SHOWN, cursor: int | None = None) -> dict:
    run = run_read(s, run_id)
    all_rows = [dict(r) for r in s.company.conn.execute(sa.select(c.bulk_edit_rows).where(
        c.bulk_edit_rows.c.run_id == run_id).order_by(c.bulk_edit_rows.c.seq)).mappings()]
    counts = {'selected': run['record_count'], 'unchanged': run['unchanged_count'],
              'change': sum(r['status'] == 'change' for r in all_rows),
              'refused': sum(r['status'] == 'refused' for r in all_rows)}
    counts['pending'] = sum(r['status'] == 'change' and r['outcome'] is None for r in all_rows) \
        if run['state'] in ('previewed', 'applying') else 0
    for r in all_rows:
        if r['outcome']:
            counts[r['outcome']] = counts.get(r['outcome'], 0) + 1
    picked = [r for r in all_rows if (outcome is None or outcome in (r['status'], r['outcome']))
              and (cursor is None or r['seq'] > cursor)]
    page = picked[:limit]
    batch = {}
    if run['batch_id']:
        batch = batches.describe(s, batches._batch(s, run['batch_id']), with_plan=True, ctx=ctx)
    from bookflow.company.info import principal_names
    by = principal_names(s.company, {run['created_by']}).get(run['created_by'])
    state = run['state']
    out_run = {k: run[k] for k in ('id', 'kind', 'state', 'atomic', 'label', 'reason', 'created_at', 'created_by',
                                   'created_via', 'updated_at', 'batch_id')}
    out_run.update(selection=json.loads(run['selection']), asked=json.loads(run['operations']), batch=batch,
                   created_by_name=by)
    nxt = (f'bulk-edit apply {run_id}' if state == 'previewed' and counts['change'] else
           f'bulk-edit apply {run_id} (or --cancel)' if state == 'applying' else
           f'bulk-edit reverse {run_id} --dry-run' if batch.get('state') == 'live' else None)
    return {'run': out_run, 'counts': counts, 'totals': _totals(s, run, all_rows), 'rows': [_row_out(r) for r in page],
            'next_cursor': page[-1]['seq'] if len(picked) > len(page) else None,
            'skipped': _skipped(s, run, batch), 'next': nxt}


def query(s, ctx, inp) -> dict:
    t = c.bulk_edit_runs
    q = sa.select(t, sa.literal_column('bulk_edit_runs.rowid').label('rowid')).order_by(
        sa.literal_column('bulk_edit_runs.rowid').desc())
    if inp.kind:
        q = q.where(t.c.kind == inp.kind)
    if inp.state:
        q = q.where(t.c.state == inp.state)
    if inp.cursor:
        try:
            q = q.where(sa.literal_column('bulk_edit_runs.rowid') < int(inp.cursor))
        except ValueError:
            raise BookflowError('E_VALIDATION', message='Not a cursor this query gave.',
                                details={'field': 'cursor'}) from None
    readable: dict[str, bool] = {}
    items, last = [], None
    for row in s.company.conn.execute(q).mappings():
        if row['kind'] not in readable:
            try:
                batches.require_read(s, f"{_kind(row['kind']).noun} show")
                readable[row['kind']] = True
            except BookflowError:
                readable[row['kind']] = False
        if not readable[row['kind']]:
            continue
        if len(items) == inp.limit:
            break
        state = s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == row['batch_id'])).scalar() \
            if row['batch_id'] else None
        n = dict(s.company.conn.execute(sa.select(c.bulk_edit_rows.c.status, sa.func.count()).where(
            c.bulk_edit_rows.c.run_id == row['id']).group_by(c.bulk_edit_rows.c.status)).all())
        items.append({'id': row['id'], 'kind': row['kind'], 'state': row['state'], 'label': row['label'],
                      'records': row['record_count'], 'change': n.get('change', 0), 'refused': n.get('refused', 0),
                      'created_at': row['created_at'], 'created_by': row['created_by'], 'batch_state': state})
        last = row['rowid']
    more = len(items) == inp.limit and last is not None and s.company.conn.execute(
        sa.select(sa.func.count()).select_from(t).where(sa.literal_column('bulk_edit_runs.rowid') < last)).scalar()
    return {'items': items, 'next_cursor': str(last) if more else None}


def column_list(s, kind_name: str) -> dict:
    k = _kind(kind_name)
    batches.require_read(s, f'{k.noun} show')
    return {'kind': kind_name, 'columns': columns(s, kind_name)}
