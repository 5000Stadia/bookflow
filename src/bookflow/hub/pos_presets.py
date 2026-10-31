"""The POS clerk presets (R114 increment 4, design/point-of-sale.md §9).

A preset in the R187 sense: an editable list applied in one step. Role `readonly`; grants of the POS capabilities it
names; a deny of every company capability the `readonly` role holds except `company` (every page runs `company show`)
and `item` (looking items up), computed from the live catalog when applied; Home = the POS page. Its effective set is
therefore `company` and `item` (read) and its grants. The installation-level capabilities the role holds (signing in,
tokens, one's own Home and memberships) are not company permissions and are left alone.

The membership records which preset it came from with a marker grant, `preset.pos-clerk` or
`preset.pos-clerk-returns` (pos-v1 registers both; no command requires them, so they give nothing). When
`permission activate` moves a root to a catalog under which `readonly` holds a company capability it did not hold
before, it adds that capability to the denies of every live membership carrying a marker, in the same command, and
reports each one (`presets_extended`): a clerk saved before a later layer never gains what the layer adds.

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


MARKER = 'preset.'


def marker(name: str) -> str:
    """The grant recording a preset on the membership (`preset.pos-clerk`): a capability no command requires, so it
    gives nothing; it says which preset the membership was made from, so a later catalog can keep it narrow."""
    return MARKER + name


def preset_of(grants) -> str | None:
    return next((g[len(MARKER):] for g in grants or () if g.startswith(MARKER) and g[len(MARKER):] in PRESETS), None)


def new_capabilities(old_catalog, new_catalog) -> set[str]:
    """Company capabilities `readonly` holds under the new catalog and did not under the old (or that are new)."""
    return set(denies(new_catalog)) - set(denies(old_catalog))


def extensions(hub, old_catalog, new_catalog) -> list[dict]:
    """Every live membership made from a preset that the new catalog would widen: what to add to its denies."""
    added = new_capabilities(old_catalog, new_catalog)
    if not added:
        return []
    import json
    out = []
    for row in hub.raw.execute(
            "SELECT m.id, m.user_id, u.username, m.scope_type, m.scope_id, m.role, m.grants, m.denies, m.version "
            "FROM main.memberships m JOIN main.users u ON u.id = m.user_id WHERE m.revoked_at IS NULL"):
        grants, held = json.loads(row[6] or '[]'), json.loads(row[7] or '[]')
        name = preset_of(grants)
        if name is None or row[5] != ROLE:  # moved off readonly: no longer a preset clerk
            continue
        missing = sorted(added - set(held) - set(grants))
        if missing:
            out.append(dict(membership_id=row[0], user_id=row[1], username=row[2], scope_type=row[3],
                            scope_id=row[4], role=row[5], grants=grants, denies=sorted({*held, *missing}),
                            version=row[8], preset=name, added=missing))
    return out


def preset(name: str, catalog=None) -> dict:
    if name not in PRESETS:
        from bookflow.core.errors import BookflowError
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'preset', 'problem': 'unknown preset'}]})
    label, grants = PRESETS[name]
    return dict(name=name, label=label, role=ROLE, grants=[*grants, marker(name)], denies=denies(catalog), home=HOME)


def kept_through(role: str, grants, preset_name: str, catalog=None) -> list[str]:
    """What a person keeps in a company through another membership (an organization's) after a preset narrows the
    company one: the company capabilities that role or those grants give, less the preset's denies and its own set.
    A deny at the company wins over the organization, so only what `readonly` never held survives."""
    if catalog is None:
        from bookflow.hub import permission_runtime
        catalog = permission_runtime.current_catalog().CATALOG
    chosen = preset(preset_name, catalog)
    held = {d.requirement.capability for d in catalog.defaults if d.role == role} | set(grants or ())
    gone = set(chosen['denies']) | set(KEEPS) | set(chosen['grants'])
    return sorted((held & company_capabilities(catalog)) - gone)
