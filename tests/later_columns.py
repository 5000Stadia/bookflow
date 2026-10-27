"""Columns a later company migration added to a table an earlier revision froze.

An earlier revision's frozen CREATE TABLE can equal today's metadata only once the columns later
revisions added (co0089's site ids, co0091's sub-cent fractions, ...) are taken back out. Which
columns those are is read from the later migrations' own ALTER TABLE ... ADD COLUMN statements,
never listed here: a literal list is what makes the next migration falsify a frozen test.
"""
import importlib
import re

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from bookflow.storage.migrate import _revision_parents

_ADD = re.compile(r'ALTER TABLE\s+"?(\w+)"?\s+ADD COLUMN\s+"?(\w+)"?', re.IGNORECASE)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _strings(item)


def _modules(chain):
    package = {'company': 'company_migrations', 'hub': 'hub_migrations'}[chain]
    folder = importlib.import_module(f'bookflow.storage.{package}.versions').__path__[0]
    from pathlib import Path
    found = {}
    for path in sorted(Path(folder).glob('[0-9]*.py')):
        module = importlib.import_module(f'bookflow.storage.{package}.versions.{path.stem}')
        found[module.revision] = module
    return found


def _head(parents):
    # Read from the shipped revision graph, not HEADS: tests pin HEADS to stop part-way.
    head, = set(parents) - set(parents.values())
    return head


def added_after(revision, chain='company'):
    """{table: {column, ...}} added by every revision after `revision`, up to the head."""
    added = {}
    for module in later_modules(revision, chain):
        for value in vars(module).values():
            for text in _strings(value):
                for table, column in _ADD.findall(text):
                    added.setdefault(table, set()).add(column)
    return added


def create_table_as_of(table, revision, chain='company'):
    """Today's CREATE TABLE for `table`, less the columns revisions after `revision` added."""
    text = str(CreateTable(table).compile(dialect=dialect())).strip()
    later = added_after(revision, chain).get(table.name, set())
    if not later:
        return text
    lines = [line for line in text.split('\n')
             if not any(re.search(rf'\b{column}\b', line) for column in later)]
    # The last remaining element before the closing parenthesis carries no separator.
    if lines[-1] == ')' and lines[-2].endswith(', '):
        lines[-2] = lines[-2][:-2]
    return '\n'.join(lines)


def later_modules(revision, chain='company'):
    """The revisions after `revision` up to the head, newest first."""
    parents, modules = _revision_parents(chain), _modules(chain)
    later, current = [], _head(parents)
    while current != revision:
        if current is None:
            raise ValueError(f'{revision} is not an ancestor of the {chain} head')
        later.append(modules[current])
        current = parents[current]
    return later


def undo_later_rewrites(table_name, text, revision, chain='company'):
    """Today's stored text for a table with each later revision's REPLACEMENTS undone, newest first."""
    for module in later_modules(revision, chain):
        for old, new in getattr(module, 'REPLACEMENTS', {}).get(table_name, ()):
            text = text.replace(new, old)
    return text
