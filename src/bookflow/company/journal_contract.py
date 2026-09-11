"""Which lists a journal line's pickers search.

Declaration only. Nothing here reads, writes or decides anything; it tells a form that the
account a line is posted to is an account and that the line's class is a class -- the two
references ``journals.line_values`` resolves for every line it writes.

A journal has no header reference at all: a journal entry names nothing above its grid, so
everything declared here sits on ``lines``.

The line's **party** is deliberately absent. ``name_id`` is a reference into whichever of the
four name lists ``name_type`` chooses, and a discriminated picker is resolved against a
control at the top of the form -- ``check_contract``'s ``pay_to.name_id`` is the shape that
works, and the money-out contract omits its own ``expenses.party`` for the same reason. A
line grid has one discriminator per row and the form cannot yet read one, so declaring the
party here would put an edge in the memorized dependency index that no form reads: a second
schema by another name. It is declared the day the grid can render it.
"""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class JournalFormDefinition:
    references: tuple[ReferenceDefinition, ...]


FORM = JournalFormDefinition((
    ReferenceDefinition('lines.account', 'account'),
    ReferenceDefinition('lines.class_id', 'class'),
))
