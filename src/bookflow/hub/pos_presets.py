"""The POS clerk presets (R114 increment 4, design/point-of-sale.md §9).

A preset in the R187 sense: an editable list applied in one step. Role `readonly`; grants of the POS capabilities it
names; a deny of every company capability the `readonly` role holds except `company` (every page runs `company show`)
and `item` (looking items up), computed from the live catalog when applied; Home = the POS page. Its effective set is
therefore `company` and `item` (read) and its grants. The installation-level capabilities the role holds (signing in,
tokens, one's own Home and memberships) are not company permissions and are left alone.

`tests/test_pos_layer.py` pins the deny list and asserts the clerk's effective set on the current catalog, so a
capability added later to `readonly` shows up as a failing test, not as a page a clerk can read.
"""
from __future__ import annotations

ROLE = 'readonly'
KEEPS = ('company', 'item')
HOME = 'pos'
PRESETS = {
    'pos-clerk': ('POS clerk', ('pos.sell',)),
    'pos-clerk-returns': ('Clerk with returns', ('pos.sell', 'pos.void', 'pos.refund')),
}


def company_capabilities(catalog) -> set[str]:
    """Every capability a company command or company action requires."""
    found = set()
    for command in catalog.commands:
        if command.routed_scope == 'company':
            found.add(command.capability)
            found |= {r.capability for r in command.resources}
    for action in catalog.company_actions:
        found |= {r.capability for r in action.requirements}
    return found


def denies(catalog=None) -> list[str]:
    if catalog is None:
        from bookflow.hub import permission_runtime
        catalog = permission_runtime.current_catalog().CATALOG
    held = {d.requirement.capability for d in catalog.defaults if d.role == ROLE}
    return sorted((held & company_capabilities(catalog)) - set(KEEPS))


def preset(name: str, catalog=None) -> dict:
    if name not in PRESETS:
        from bookflow.core.errors import BookflowError
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'preset', 'problem': 'unknown preset'}]})
    label, grants = PRESETS[name]
    return dict(name=name, label=label, role=ROLE, grants=list(grants), denies=denies(catalog), home=HOME)
