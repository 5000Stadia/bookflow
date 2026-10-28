"""Saved import mappings and decisions (R107 increment 2, design/bulk-import.md §4 and §2 "References that do not exist").

A **source** is a named origin of files for one kind ("Square customer export"); its **mapping** is the recipe for
those files (columns to input paths, constants, formats, key, mode, value maps), saved from a run and changed only by
saving a new version (``import_mappings`` is append-only).

**Matching a file.** Headers are normalized (trim, case-fold, collapsed spaces); the fingerprint is the hash of their
sorted set. An **exact** match (same set, same kind, an active source) is used without asking; two sources with the
same headers ask which (``mapping``). A **probable** match (every mapped header present and at least 80 percent of
headers shared) is offered with its diff and waits for a person or agent to confirm it (``mapping NAME``). A mapped
header that is absent never matches, so a renamed Amount column cannot post the wrong column.

**Decisions.** An unknown reference asks: `import decide` maps it to an existing record, creates it through its own
create command when the run reaches it (never an account or item; a customer or vendor only when nothing looks like
it), or skips its rows. ``remember`` saves a map in the source's value maps as a new version, so next month's file does
not ask again.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, import_kinds as kinds, schema as c, tabular
from bookflow.core.ids import new_id

PROBABLE_OVERLAP = 0.8


def _norm(text: str) -> str:
    return tabular.normalize_header(text)


def fingerprint(headers: list[str]) -> str:
    keys = sorted({_norm(h) for h in headers if _norm(h)})
    return hashlib.sha256('\n'.join(keys).encode('utf-8')).hexdigest()


def _source_row(s, name: str, *, required: bool = True) -> dict | None:
    from bookflow.company.imports import norm_key
    row = s.company.conn.execute(sa.select(c.import_sources).where(
        c.import_sources.c.name_key == norm_key(name))).mappings().first()
    if row is None and required:
        raise BookflowError('E_RECORD_NOT_FOUND', message=f'No saved import source {name!r}; `import-mapping query` '
                            'lists them.', details={'record_type': 'import_source', 'selector': name,
                                                    'field': 'source'})
    return dict(row) if row else None


def _mapping_out(row, source: dict) -> dict:
    return {'id': row['id'], 'source': source['name'], 'kind': source['kind'], 'version': row['version'],
            'fingerprint': row['header_fingerprint'], 'headers': json.loads(row['headers']),
            'columns': json.loads(row['columns']), 'formats': json.loads(row['formats']),
            'key': json.loads(row['key_paths']), 'mode': row['mode'], 'on_unknown': row['on_unknown'],
            'value_maps': json.loads(row['value_maps']), 'defaults': json.loads(row['defaults']),
            'run': row['run_id'], 'created_at': row['created_at'], 'created_by': row['created_by']}


def _source_out(source: dict) -> dict:
    return {k: source[k] for k in ('id', 'name', 'kind', 'active', 'version', 'created_at', 'created_by',
                                   'updated_at', 'updated_by')}


def latest(s, source: dict) -> dict:
    row = s.company.conn.execute(sa.select(c.import_mappings).where(
        c.import_mappings.c.source_id == source['id'], c.import_mappings.c.version == source['version'])
    ).mappings().first()
    return _mapping_out(row, source)


def candidates(s, kind: str, source: str | None = None) -> list[dict]:
    """The latest mapping of each active source of the kind (or of the one named)."""
    q = sa.select(c.import_sources).where(c.import_sources.c.kind == kind, c.import_sources.c.active.is_(True))
    if source:
        from bookflow.company.imports import norm_key
        q = q.where(c.import_sources.c.name_key == norm_key(source))
    return [latest(s, dict(row)) for row in s.company.conn.execute(q.order_by(c.import_sources.c.name)).mappings()]


def diff(saved: dict, headers: list[str]) -> dict:
    """How a file's headers differ from a saved mapping's: new columns, missing ones, and missing mapped ones."""
    have = {_norm(h) for h in headers if _norm(h)}
    was = {_norm(h): h for h in saved['headers'] if _norm(h)}
    mapped = {_norm(h) for h in saved['columns']}
    union = have | set(was)
    return {'new': [h for h in headers if _norm(h) and _norm(h) not in was],
            'missing': [h for k, h in was.items() if k not in have],
            'missing_mapped': [h for h in saved['columns'] if _norm(h) not in have],
            'overlap': round(len(have & set(was)) / len(union), 3) if union else 1.0}


def match(s, kind: str, headers: list[str], source: str | None = None) -> list[dict]:
    fp = fingerprint(headers)
    out = []
    for saved in candidates(s, kind, source):
        d = diff(saved, headers)
        how = ('exact' if saved['fingerprint'] == fp else
               'probable' if not d['missing_mapped'] and d['overlap'] >= PROBABLE_OVERLAP else 'none')
        out.append({'source': saved['source'], 'version': saved['version'], 'match': how, **d})
    rank = {'exact': 0, 'probable': 1, 'none': 2}
    return sorted(out, key=lambda m: (rank[m['match']], -m['overlap'], m['source']))


def for_file(s, kind: kinds.Kind, table: tabular.Table, inp) -> tuple[dict | None, dict | None, dict | None]:
    """(the saved mapping to use, how it matched, a decision the run waits on) for a file being checked or previewed."""
    name = getattr(inp, 'mapping', None)
    if name:
        source = _source_row(s, name)
        if source['kind'] != kind.name or not source['active']:
            raise BookflowError('E_VALIDATION', message=(
                f"Source {source['name']!r} is " + (f"a {source['kind']} mapping" if source['kind'] != kind.name
                                                    else 'deactivated') + '; it cannot map this file.'),
                details={'field': 'mapping', 'kind': source['kind'], 'active': source['active']})
        if inp.source and _norm(inp.source) != _norm(source['name']):
            raise BookflowError('E_VALIDATION', message='`mapping` names the source whose keys the rows join; give '
                                'one of `mapping` and `source`, or the same name in both.', details={'field': 'source'})
        saved = latest(s, source)
        d = diff(saved, table.headers)
        if d['missing_mapped']:
            raise BookflowError('E_VALIDATION', message=(
                f"The file lacks column(s) the mapping of {source['name']!r} maps: "
                + ', '.join(repr(h) for h in d['missing_mapped']) + '. A renamed or missing column never matches '
                'silently; map this file by hand (`columns`) or rename the column back.'),
                details={'field': 'mapping', **d})
        how = 'exact' if saved['fingerprint'] == fingerprint(table.headers) else 'confirmed'
        return {**saved, 'match': how}, {'source': saved['source'], 'version': saved['version'], 'match': how, **d}, None
    if inp.columns:
        return None, None, None  # mapped by hand
    found = [m for m in match(s, kind.name, table.headers, inp.source) if m['match'] != 'none']
    exact = [m for m in found if m['match'] == 'exact']
    if len(exact) == 1:
        saved = latest(s, _source_row(s, exact[0]['source']))
        return {**saved, 'match': 'exact'}, exact[0], None
    if exact:
        names = ', '.join(repr(m['source']) for m in exact)
        return None, {'match': 'several', 'candidates': exact}, {
            'decision': 'mapping', 'candidates': exact,
            'message': f'The saved mappings of {names} all fit these headers; preview again with `mapping` naming '
                       'the source this file comes from.'}
    if found:
        top = found[0]
        new = ', '.join(top['new']) or 'none'
        missing = ', '.join(top['missing']) or 'none'
        return None, {'match': 'probable', 'candidates': found}, {
            'decision': 'mapping', 'candidates': found,
            'message': f"The saved mapping of {top['source']!r} (version {top['version']}) probably fits: new "
                       f"{new}; missing {missing}. Preview again with mapping {top['source']!r} to use it, or with "
                       '`columns` to map by hand.'}
    return None, None, None


# ------------------------------------------------------------------ saving and reading

def _from_run(run: dict) -> dict:
    stored = json.loads(run['mapping'])
    formats = dict(stored.get('formats') or {})
    formats.update({'sheet': stored.get('sheet'), 'delimiter': run['delimiter']})
    return {'headers': stored.get('headers') or [], 'columns': stored.get('columns') or {}, 'formats': formats,
            'key': [] if stored.get('keyed_by_content') else list(stored.get('key') or []), 'mode': run['mode'],
            'value_maps': stored.get('value_maps') or {}, 'defaults': stored.get('constants') or {}}


_SAME = ('headers', 'columns', 'formats', 'key', 'mode', 'value_maps', 'defaults')


def default_source(kind: str) -> str:
    """The source a file is keyed under when none is named: shared by every such file of the kind."""
    return f'{kind} import'


def plan_save(s, ctx, source_name: str, recipe: dict, kind: str) -> dict:
    """What saving would do: a new source, a new version, or nothing when the recipe is the latest one."""
    if _norm(source_name) == _norm(default_source(kind)):
        raise BookflowError('E_VALIDATION', message=(
            f"{source_name!r} is the name every {kind} file without a source is keyed under, so a mapping saved there "
            "would match any file with these headers; save it under the name of where the file comes from "
            '("Acme export").'), details={'field': 'source', 'value': source_name})
    source = _source_row(s, source_name, required=False)
    if source is not None:
        if source['kind'] != kind:
            raise BookflowError('E_VALIDATION', message=(f"Source {source['name']!r} holds {source['kind']} files; "
                                                         f'save this {kind} mapping under another name.'),
                                details={'field': 'source', 'kind': source['kind']})
        if not source['active']:
            raise BookflowError('E_VALIDATION', message=f"Source {source['name']!r} is deactivated; save under "
                                'another name.', details={'field': 'source'})
        current = latest(s, source)
        if all(current[k] == recipe[k] for k in _SAME):
            return {'action': 'unchanged', 'source': source, 'current': current}
        return {'action': 'version', 'source': source, 'version': source['version'] + 1}
    return {'action': 'create', 'source': None, 'version': 1}


def apply_save(s, ctx, source_name: str, recipe: dict, kind: str, plan: dict, run_id: str | None) -> dict:
    from bookflow.company.imports import norm_key
    if plan['action'] == 'unchanged':
        return {'source': _source_out(plan['source']), 'mapping': plan['current'], 'saved': False}
    at = batches.now()
    source = plan['source']
    if source is None:
        source = {'id': new_id(), 'name': source_name.strip(), 'name_key': norm_key(source_name), 'kind': kind,
                  'active': True, 'version': 1, 'created_at': at, 'created_by': s.actor.id, 'updated_at': at,
                  'updated_by': s.actor.id}
        s.company.conn.execute(sa.insert(c.import_sources).values(**source))
    else:
        changed = s.company.conn.execute(sa.update(c.import_sources).where(
            c.import_sources.c.id == source['id'], c.import_sources.c.version == source['version']).values(
            version=plan['version'], updated_at=at, updated_by=s.actor.id))
        if changed.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', message='The source was saved meanwhile; save again.',
                                details={'field': 'source'})
        source = {**source, 'version': plan['version'], 'updated_at': at, 'updated_by': s.actor.id}
    row = {'id': new_id(), 'source_id': source['id'], 'version': plan['version'],
           'header_fingerprint': fingerprint(recipe['headers']), 'headers': json.dumps(recipe['headers']),
           'columns': json.dumps(recipe['columns'], sort_keys=True),
           'formats': json.dumps(recipe['formats'], sort_keys=True), 'key_paths': json.dumps(recipe['key']),
           'mode': recipe['mode'], 'on_unknown': 'ask', 'value_maps': json.dumps(recipe['value_maps'], sort_keys=True),
           'defaults': json.dumps(recipe['defaults'], sort_keys=True), 'run_id': run_id, 'created_at': at,
           'created_by': s.actor.id}
    s.company.conn.execute(sa.insert(c.import_mappings).values(**row))
    return {'source': _source_out(source), 'mapping': _mapping_out(row, source), 'saved': True}


def recipe_from_run(s, run: dict, source_name: str) -> dict:
    """The run's recipe, keeping the value maps the source already holds (remembered decisions) under the run's own."""
    recipe = _from_run(run)
    source = _source_row(s, source_name, required=False)
    if source is not None and source['active'] and source['kind'] == run['kind']:
        held = latest(s, source)['value_maps']
        recipe['value_maps'] = {p: {**held.get(p, {}), **recipe['value_maps'].get(p, {})}
                                for p in set(held) | set(recipe['value_maps'])}
    return recipe


def _readable_source(s, name: str) -> dict:
    """A saved source, if the caller may read its kind's records: its value maps and headers are drawn from them."""
    source = _source_row(s, name)
    batches.require_read(s, f"{kinds.kind(source['kind']).noun} show")
    return source


def show(s, name: str, version: int | None = None) -> dict:
    source = _readable_source(s, name)
    if version is None:
        mapping = latest(s, source)
    else:
        row = s.company.conn.execute(sa.select(c.import_mappings).where(
            c.import_mappings.c.source_id == source['id'], c.import_mappings.c.version == version)).mappings().first()
        if row is None:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'import_mapping', 'field': 'version',
                                                               'selector': f"{source['name']} v{version}"})
        mapping = _mapping_out(row, source)
    return {'source': _source_out(source), 'mapping': mapping, 'saved': False}


def history(s, name: str) -> dict:
    source = _readable_source(s, name)
    rows = s.company.conn.execute(sa.select(c.import_mappings).where(
        c.import_mappings.c.source_id == source['id']).order_by(c.import_mappings.c.version.desc())).mappings()
    return {'source': _source_out(source), 'versions': [_mapping_out(r, source) for r in rows]}


def query(s, kind: str | None, active: bool | None, limit: int, cursor: str | None) -> dict:
    readable = []
    for name, k in kinds.KINDS.items():  # only sources of kinds whose records the caller may read
        try:
            batches.require_read(s, f'{k.noun} show')
        except BookflowError:
            continue
        readable.append(name)
    q = sa.select(c.import_sources).where(c.import_sources.c.kind.in_(readable)).order_by(
        c.import_sources.c.name_key)
    if kind:
        q = q.where(c.import_sources.c.kind == kind)
    if active is not None:
        q = q.where(c.import_sources.c.active.is_(active))
    if cursor:
        q = q.where(c.import_sources.c.name_key > cursor)
    rows = [dict(r) for r in s.company.conn.execute(q.limit(limit + 1)).mappings()]
    more = len(rows) > limit
    rows = rows[:limit]
    return {'items': [_source_out(r) for r in rows], 'next_cursor': rows[-1]['name_key'] if more and rows else None}


def plan_deactivate(s, name: str) -> dict:
    source = _readable_source(s, name)
    if not source['active']:
        raise BookflowError('E_VALIDATION', message=f"Source {source['name']!r} is already deactivated.",
                            details={'field': 'source'})
    return source


def apply_deactivate(s, source: dict) -> dict:
    at = batches.now()
    changed = s.company.conn.execute(sa.update(c.import_sources).where(
        c.import_sources.c.id == source['id'], c.import_sources.c.active.is_(True)).values(
        active=False, updated_at=at, updated_by=s.actor.id))
    if changed.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', message='The source changed meanwhile.', details={'field': 'source'})
    source = {**source, 'active': False, 'updated_at': at, 'updated_by': s.actor.id}
    return {'source': _source_out(source), 'mapping': latest(s, source), 'saved': False}


# ------------------------------------------------------------------ decisions

def _find(pending: list[dict], text: str, what: str) -> tuple[dict, str]:
    """The pending reference a decision names: by its file text, or `Column:text` when two columns share it."""
    from bookflow.company.imports import norm_key
    hits = [r for r in pending if norm_key(r['value']) == norm_key(text)]
    if not hits and ':' in text:
        column, value = text.split(':', 1)
        hits = [r for r in pending if norm_key(r['value']) == norm_key(value)
                and norm_key(column) in (norm_key(r['column'] or ''), norm_key(r['field']))]
        text = value
    if len(hits) == 1:
        return hits[0], text.strip()
    listed = '; '.join(f"{r['column'] or r['field']}:{r['value']}" for r in pending[:20]) or 'none'
    if not hits:
        raise BookflowError('E_VALIDATION', message=f'{what} {text!r} names no unknown reference this run waits on '
                            f'(waiting: {listed}).', details={'field': what, 'value': text})
    raise BookflowError('E_VALIDATION', message=f'{text!r} is unknown in more than one column; write it as '
                        f'"Column:{text}".', details={'field': what, 'value': text})


def plan_decide(s, ctx, inp) -> dict:
    from bookflow.company import imports
    from bookflow.company.imports import norm_key
    run = imports._run_read(s, inp.run)
    if run['batch_id'] is not None or run['state'] not in ('waiting', 'previewed'):
        raise BookflowError('E_VALIDATION', message=f"Run {run['id']} is {run['state']}; decisions are made before "
                            'it runs. Preview the file again.', details={'field': 'run', 'state': run['state']})
    kind = kinds.kind(run['kind'])
    pending = imports.pending_references(s, run)
    books = imports.Books(s, kind, run['source'])
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(ref, action, target=None, extra=None):
        key = (ref['field'], norm_key(ref['value']))
        if key in seen:
            raise BookflowError('E_VALIDATION', message=f"{ref['value']!r} is decided twice in this call.",
                                details={'field': action, 'value': ref['value']})
        seen.add(key)
        out.append({'ref': ref, 'action': action, 'target': target, 'input': extra})

    asked: list[tuple[dict, str, str | None, dict | None]] = []
    for text in inp.map or ():
        if '=' not in text:
            raise BookflowError('E_VALIDATION', message=f'A map is "file text=existing name"; got {text!r}.',
                                details={'field': 'map', 'value': text})
        left, target = text.split('=', 1)
        ref, _ = _find(pending, left, 'map')
        asked.append((ref, 'map', target.strip(), None))
    given = {norm_key(k): dict(v) for k, v in (inp.create_input or {}).items()}
    for text in inp.create or ():
        ref, _ = _find(pending, text, 'create')
        asked.append((ref, 'create', None, given.get(norm_key(text)) or given.get(norm_key(ref['value']))))
    for text in inp.skip or ():
        ref, _ = _find(pending, text, 'skip')
        asked.append((ref, 'skip', None, None))
    for a in inp.answers or ():
        # As data: the field and the exact text name the reference, so no text needs quoting.
        hits = [r for r in pending if r['field'] == a.field and norm_key(r['value']) == norm_key(a.value)]
        if not hits:
            listed = '; '.join(f"{r['column'] or r['field']}:{r['value']}" for r in pending[:20]) or 'none'
            raise BookflowError('E_VALIDATION', message=f'{a.value!r} in {a.field} names no unknown reference this '
                                f'run waits on (waiting: {listed}).',
                                details={'field': 'answers', 'value': a.value, 'ref': {'field': a.field,
                                                                                       'value': a.value}})
        if a.action == 'map' and not (a.target or '').strip():
            raise BookflowError('E_VALIDATION', message=f'Name the existing record to use for {a.value!r}.',
                                details={'field': 'answers', 'ref': {'field': a.field, 'value': a.value}})
        asked.append((hits[0], a.action, (a.target or '').strip() or None, a.input))
    for ref, action, target, extra in asked:
        where = {'ref': {'field': ref['field'], 'value': ref['value']}}
        if action == 'map':
            # A typed name is a read of that list: a caller who may not read it cannot probe its names this way.
            batches.require_read(s, f"{ref['target']} show")
            if books.resolve(ref['target'], target) is None:
                hint = books.suggestions(ref['target'], target)
                raise BookflowError('E_VALIDATION', message=(
                    f"No {ref['target'].replace('-', ' ')} {target!r} to map {ref['value']!r} to"
                    + (f"; did you mean {', '.join(map(repr, hint))}?" if hint else '.')),
                    details={'field': 'map', 'value': target, 'suggestions': hint, **where})
            add(ref, 'map', target)
        elif action == 'create':
            extra = dict(extra or {})
            how = imports.creatable(ref['target'])
            if how == 'never':
                raise BookflowError('E_VALIDATION', message=(
                    f"An import never creates {ref['target']}s (an {ref['target']} is set up with its own command); "
                    f"map {ref['value']!r} to an existing one or skip its rows."),
                    details={'field': 'create', 'target': ref['target'], **where})
            alike = books.look_alikes(ref['target'], ref['value'])
            if ref['target'] in ('customer', 'vendor') and alike:
                raise BookflowError('E_VALIDATION', message=(
                    f"{ref['value']!r} looks like {', '.join(map(repr, alike))}; map it to one of them, or create the "
                    f"{ref['target']} with its own command if it is really new."),
                    details={'field': 'create', 'look_alikes': alike, **where})
            body = {**extra, 'name': ref['value']}
            try:
                batches.run_nested(s, ctx, f"{ref['target']} create", body, source_ref=f"import:{run['id']}",
                                   dry_run=True)
            except BookflowError as error:
                raise BookflowError('E_VALIDATION', message=(
                    f"Creating {ref['target'].replace('-', ' ')} {ref['value']!r} would be refused: {error.message} "
                    f"Give the rest of `{ref['target']} create`'s input in create_input: "
                    f'{{"{ref["value"]}": {{...}}}}.'),
                    details={'field': 'create', 'code': error.code, 'create': how, **where}) from None
            add(ref, 'create', ref['value'], extra or None)
        else:
            add(ref, 'skip')
    if not out:
        raise BookflowError('E_VALIDATION', message='Give at least one map, create or skip.', details={'field': 'map'})
    # `remember` keeps maps, in the source's value maps; creates and skips are answered for this run only (a record
    # created is found by its name next time), so a call with none has nothing to remember and is not refused.
    wanted = inp.remember and any(d['action'] == 'map' for d in out)
    remember = None
    if wanted and _norm(run['source']) == _norm(default_source(run['kind'])):
        raise BookflowError('E_VALIDATION', message=(
            f"`remember` saves the maps in the run's source, and this run names none (it is keyed under the shared "
            f"{run['source']!r}, which any {run['kind']} file would match). Preview the file with `source` naming where "
            'it comes from, then decide with `remember`; or decide without it.'),
            details={'field': 'remember', 'source': run['source']})
    if wanted:
        source = _source_row(s, run['source'], required=False)
        recipe = latest(s, source) if source is not None and source['active'] else _from_run(run)
        recipe = {k: recipe[k] for k in _SAME}
        maps = {p: dict(m) for p, m in recipe['value_maps'].items()}
        for d in out:
            if d['action'] == 'map':
                # keyed on the file's own text, before any value map changed it, so next month's file matches it
                text = d['ref'].get('file_value') or d['ref']['value']
                maps.setdefault(d['ref']['field'], {})[norm_key(text)] = d['target']
        recipe['value_maps'] = maps
        if source is None:
            recipe['headers'] = json.loads(run['mapping']).get('headers') or recipe['headers']
        remember = {'recipe': recipe, 'plan': plan_save(s, ctx, run['source'], recipe, run['kind'])}
    left = len(pending) - len(out)
    return {'run': run, 'decisions': out, 'remember': remember, 'left': left}


def apply_decide(s, ctx, plan: dict) -> dict:
    from bookflow.company import imports
    from bookflow.company.imports import norm_key
    run = plan['run']
    at = batches.now()
    for d in plan['decisions']:
        s.company.conn.execute(sa.insert(c.import_decisions).values(
            id=new_id(), run_id=run['id'], field=d['ref']['field'], value=d['ref']['value'],
            value_key=norm_key(d['ref']['value']), action=d['action'], target=d['target'],
            input=json.dumps(d['input'], sort_keys=True) if d['input'] else None,
            remembered=bool(plan['remember']) and d['action'] == 'map', created_at=at, created_by=s.actor.id))
    saved = None
    if plan['remember']:
        saved = apply_save(s, ctx, run['source'], plan['remember']['recipe'], run['kind'], plan['remember']['plan'],
                           run['id'])
    if plan['left'] == 0 and run['state'] == 'waiting':
        s.company.conn.execute(sa.update(c.import_runs).where(c.import_runs.c.id == run['id']).values(
            state='previewed', version=c.import_runs.c.version + 1, updated_at=at))
    out = imports.show(s, ctx, run['id'])
    if saved is not None and saved['saved']:
        out['notes'].append(f"Remembered in {saved['source']['name']!r} version {saved['mapping']['version']}: the "
                            'next file from this source maps these values without asking.')
    return out
