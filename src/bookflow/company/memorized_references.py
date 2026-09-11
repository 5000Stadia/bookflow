"""The dependency index of a memorized payload, derived -- never written out by hand.

The trap this module exists to avoid is a second document schema. If the edges a template
depends on were listed here, per command, that list would be a parallel description of every
document's shape, and it would drift from the real one the first time a field moved. So
nothing here knows what an invoice or a bill contains.

It reads the **same declarations the ordinary form and the ordinary command resolve through**:
``registry.noun_meta(noun)`` yields either a document's ``form_definition`` or a list's own
``ListDefinition``, and both carry ``references`` -- the ``ReferenceDefinition`` tuples in
``sales_contract``, ``bill_contract``, ``check_contract``, ``transfer_contract`` and
``lists``. Walking a payload against those declarations is the whole derivation.

Two honesty rules follow from that, and both are visible in the output rather than hidden:

* **A command that declares no reference paths gets an empty index**, and its revision records
  ``reference_schema = 'none'``. Several posting commands are in that state today. An empty
  index is not a claim that a template depends on nothing; it is a statement that the command
  has not declared where its references are.
* **A value whose target list cannot be determined stays exactly as it was typed**, and the
  capture returns a warning naming the path -- a multi-target reference whose discriminator the
  payload does not set has no list to search. The index would rather say so than invent an edge.
  A value whose list *is* known and which does not name a row there is refused outright, because
  a template pointing at a record that is not there is broken from birth.

Resolution itself is the ordinary one: ``list_service.resolve_selector`` against the target
list's own table and definition -- the same call ``bills``, ``refunds`` and ``bill_payments``
make. That is what lets a template survive a rename: what is stored is the id the selector
resolved to, not the name that was typed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bookflow.company import list_service, schema as c
from bookflow.company.lists import get_list_definition
from bookflow.core.errors import BookflowError

# The bound of this increment, declared once. Replaying an update or a void is a different
# problem -- it needs a target document that may have moved on -- and it is out of scope here.
ENTERABLE_VERBS = ('post', 'create')


def enterable(command) -> bool:
    """Whether a registered command may be memorized, decided from what it declares."""
    return bool(
        command is not None
        and command.scope == 'company'
        and command.is_write
        and getattr(command, 'ledger', False)
        and not command.local_only
        and command.verb in ENTERABLE_VERBS
        # Idempotence per slot is the whole guarantee; a command that cannot take a retry key
        # cannot give it, so it is not memorizable however posting-shaped it looks.
        and command.accepts_idempotency_key
        # An occurrence's slot *is* its accounting date, and entry always supplies it. A
        # command that takes no date would silently date its entry from somewhere else, which
        # is the one thing a schedule must never do.
        and 'date' in command.input_model.model_fields
    )


def enterable_commands() -> list[str]:
    from bookflow.core import registry
    registry.load_all()
    return sorted(cmd.name for cmd in registry.all_commands() if enterable(cmd))


def resolve_command(name: str):
    """The registered command this template replays, or E_VALIDATION naming what is allowed."""
    from bookflow.core import registry
    command = registry.get(name) if isinstance(name, str) and name else None
    if not enterable(command):
        raise BookflowError('E_VALIDATION', details={
            'fields': [{'field': 'command', 'problem': 'not a memorizable create or post command'}],
            'allowed': enterable_commands()})
    return command


def declarations(command) -> tuple[Any, ...] | None:
    """The command's own declared reference paths, or None when it declares none."""
    from bookflow.core import registry
    meta = registry.noun_meta(command.noun)
    definition = meta.get('form_definition') or meta.get('definition')
    references = getattr(definition, 'references', None)
    return tuple(references) if references else None


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode('utf-8')).hexdigest()


def _leaves(container: Any, segments: tuple[str, ...]):
    """Yield ``(parent_object, key)`` for every place a dotted path lands, lists included."""
    if isinstance(container, list):
        for item in container:
            yield from _leaves(item, segments)
        return
    if not isinstance(container, dict):
        return
    head, rest = segments[0], segments[1:]
    if head not in container:
        return
    if not rest:
        yield container, head
        return
    yield from _leaves(container[head], rest)


def _selected(payload: dict[str, Any], parent: dict[str, Any], path: str | None) -> str | None:
    """Read a discriminator: from the row that holds the leaf first, then from the top."""
    if not path:
        return None
    last = path.split('.')[-1]
    if isinstance(parent, dict) and parent.get(last) not in (None, ''):
        return str(parent[last])
    for holder, key in _leaves(payload, tuple(path.split('.'))):
        value = holder.get(key)
        if value not in (None, ''):
            return str(value)
    return None


def _target_noun(reference, payload, parent) -> str | None:
    nouns = reference.target_nouns
    if len(nouns) == 1:
        return nouns[0]
    chosen = _selected(payload, parent, getattr(reference, 'discriminator', None))
    normalized = str(chosen).replace('_', '-') if chosen else None
    return normalized if normalized in nouns else None


def capture(session, command, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str, list[str]]:
    """Resolve the payload's references to stable ids and derive the index rows for them.

    Returns the payload with ids in place, the index rows, whether the command declares a
    reference schema at all, and one warning per path that would not resolve.
    """
    references = declarations(command)
    if references is None:
        return payload, [], 'none', []
    edges: list[dict[str, Any]] = []
    warnings: list[str] = []
    for reference in references:
        if getattr(reference, 'many', False) or getattr(reference, 'owned_collection', None):
            # A family of columns, or an id owned by another record rather than naming one:
            # neither is a single scalar pointing at a list, so neither is indexable here.
            continue
        ordinal = 0
        for parent, key in _leaves(payload, tuple(reference.field.split('.'))):
            value = parent.get(key)
            if value in (None, ''):
                continue
            noun = _target_noun(reference, payload, parent)
            definition = get_list_definition(noun) if noun else None
            if definition is None:
                warnings.append(f'{reference.field}: no list to resolve {value!r} against; left as typed')
                ordinal += 1
                continue
            table = c.metadata.tables[definition.table]
            try:
                row = list_service.resolve_selector(session.company, table, definition, value)
            except BookflowError as error:
                # A template that names a record which is not there is broken from birth, and
                # finding that out at the first slot is a month too late.
                error.details['field'] = reference.field
                error.details['target'] = noun
                raise
            parent[key] = row[definition.identifier]
            edges.append({
                'field_path': reference.field[:120], 'ordinal': ordinal, 'target_noun': noun,
                'target_record_type': definition.record_type, 'target_id': row[definition.identifier],
                'target_active': bool(row.get('active', True)),
            })
            ordinal += 1
    return payload, edges, 'declared', warnings


def current_state(session, edges) -> list[dict[str, Any]]:
    """What each indexed row looks like now: present, active, and what it is called.

    This is the whole point of keeping the index. It makes a deactivated account or a deleted
    class visible before the next slot comes due; it never decides whether entry may proceed,
    because only the command itself can prove that.
    """
    out = []
    for edge in edges:
        definition = get_list_definition(edge['target_noun'])
        row = None
        if definition is not None:
            table = c.metadata.tables[definition.table]
            import sqlalchemy as sa
            row = session.company.conn.execute(
                sa.select(table).where(table.c[definition.identifier] == edge['target_id'])
            ).mappings().first()
        out.append({
            'field_path': edge['field_path'], 'ordinal': edge['ordinal'],
            'target_noun': edge['target_noun'], 'target_id': edge['target_id'],
            'name': (row or {}).get(definition.display_field) if definition is not None and row else None,
            'present': row is not None,
            'active': bool(row['active']) if row is not None and 'active' in row else True,
            'active_when_captured': bool(edge['target_active']),
        })
    return out
