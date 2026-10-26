"""R180: the indexed permission comparison answers exactly as the scanning one did.

permission_policy indexes each phase's facts once per comparison (visibility, memberships,
admin action domains). tests/permission_policy_oracle.py keeps the scanning implementation it
replaced. On random rosters, policies, catalogs and semantics, every public question (compare,
signature, admissions, company_action, execution) must get an identical answer from both.
"""
import random
from dataclasses import replace

import pytest

from bookflow.hub import permission_policy as p
from bookflow.hub.permission_catalog import (AdminAction, Catalog, CapabilitySpec, CompanyAction,
    DefaultEntry, ROLES, Requirement as R, ScopeKey as S, catalog_manifest)
from tests import permission_policy_oracle as oracle

CAPS = ('ledger.read', 'ledger.post', 'customer-work', 'transaction.invoice.delete', 'transaction.payment.delete')
THRESH = ('member', 'standard', 'admin', 'owner')


def _catalog(rng, version):
    specs = []
    for name in CAPS:
        if rng.random() < 0.15:
            continue
        if name.endswith('.delete'):  # the catalog's fixed Delete shape
            specs.append(CapabilitySpec(name, ('standard',), ()))
            continue
        company = tuple(sorted(rng.sample(THRESH, rng.randint(0, 2)), key=THRESH.index))
        extra = tuple(t for t in THRESH if t not in company and rng.random() < 0.2)
        registered = tuple(sorted(set(company) | set(extra), key=THRESH.index)) or ('member',)
        specs.append(CapabilitySpec(name, company, registered))
    reqs = [R(c.name, t) for c in specs for t in c.company_thresholds]
    registered = [R(c.name, t) for c in specs for t in c.registered_thresholds]
    defaults = tuple(sorted({DefaultEntry(role, req) for req in registered for role in (*ROLES, 'hub_admin')
                             if rng.random() < 0.5}, key=lambda d: (d.role, d.requirement.capability, d.requirement.threshold)))
    actions = []
    if reqs:
        actions.append(CompanyAction('post', tuple(rng.sample(reqs, min(len(reqs), rng.randint(1, 2)))),
                                     rng.random() < 0.7, ('financial-owner',) if rng.random() < 0.3 else ()))
    admin = []
    for key, domain in (('admin:users', 'hub'), ('admin:members:owner:company', 'company'),
                        ('admin:members:standard:company', 'company'), ('admin:members:standard:organization', 'organization'),
                        ('admin:moving', rng.choice(('organization', 'company')))):
        if rng.random() < 0.85:
            admin.append(AdminAction(key, domain, rng.choice(('hub_admin', *THRESH, 'authenticated')),
                                     rng.random() < 0.5, rng.random() < 0.8))
    return Catalog(version, (), tuple(specs), defaults, tuple(actions), tuple(admin), ())


def _phase_input(rng):
    people_all = [f'U{i}' for i in range(rng.randint(1, 5))] + [f'A{i}' for i in range(rng.randint(0, 2))]
    orgs_all = [f'O{i}' for i in range(rng.randint(1, 3))]
    comps_all = [f'C{i}' for i in range(rng.randint(0, 6))]
    cats = (_catalog(rng, 'v1'), _catalog(rng, 'v2') if rng.random() < 0.5 else None)
    scopes = [S('hub', 'root'), *[S('organization', i) for i in orgs_all], *[S('company', i) for i in comps_all],
              *[S('future_company', i) for i in orgs_all]]
    agents = {i for i in people_all if i.startswith('A')}

    def users():
        out = {}
        for i in people_all:
            if rng.random() < 0.85:
                out[i] = p.Subject('agent' if i in agents else 'human', rng.random() < 0.85,
                                   i.startswith('U') and rng.random() < 0.2)
        return out

    def phase(cat):
        us = users()
        org = [o for o in orgs_all if rng.random() < 0.9] or orgs_all[:1]
        comp = {c: rng.choice(org) for c in comps_all if rng.random() < 0.85}
        policies = {}
        names = [c.name for c in cat.capabilities]
        for i in us:
            for scope in scopes:
                if scope.kind not in ('organization', 'company'):
                    continue
                if not (scope.id in org if scope.kind == 'organization' else scope.id in comp):
                    continue
                if rng.random() < 0.4:
                    grants = tuple(sorted(rng.sample(names, rng.randint(0, min(2, len(names))))))
                    rest = [n for n in names if n not in grants]
                    denies = tuple(sorted(rng.sample(rest, rng.randint(0, min(1, len(rest))))))
                    policies[(i, scope)] = p.Membership(rng.choice(ROLES), p.Policy(grants, denies))
        vis = []
        for i in people_all:
            for scope in scopes:
                exists = scope.kind == 'hub' or scope.id in (comp if scope.kind == 'company' else org)
                parent = comp.get(scope.id) if scope.kind == 'company' else scope.id
                visible = scope.kind == 'hub' or (i, S('organization', parent)) in policies or (i, scope) in policies
                if scope.kind == 'organization' and rng.random() < 0.3:
                    visible = True
                if i in us and us[i].hub_admin:
                    visible = True
                if rng.random() < 0.1:
                    visible = False
                vis.append(p.Visibility(i, scope, bool(exists and i in us and visible)))
        humans = sorted(i for i, u in us.items() if u.kind == 'human' and u.active)
        agent_slots = tuple(p.AgentSlot(i, p.AgentState(rng.random() < 0.3, tuple(rng.sample(humans, rng.randint(0, len(humans)))))
                                         if i in us else None) for i in sorted(agents))
        ph = p.Phase(cat, tuple(p.OrganizationSlot(i, i in org) for i in orgs_all),
                     tuple(p.CompanySlot(i, i in comp, comp.get(i)) for i in comps_all),
                     tuple(p.SubjectSlot(i, us.get(i)) for i in people_all),
                     tuple(p.MembershipSlot(i, s, policies.get((i, s))) for i in people_all for s in scopes
                           if s.kind in ('organization', 'company')),
                     tuple(vis), agent_slots, rng.choice(('prepared_v1', 'legacy_actual', 'scoped_v1')))
        manifest = p.PhaseManifest(tuple(org), tuple(comp.items()), tuple((i, u.kind) for i, u in us.items()),
                                   catalog_manifest(cat))
        return ph, manifest

    old, om = phase(cats[0])
    new, nm = phase(cats[1] or cats[0])
    return p.ComparisonInput(p.Expected(om, nm), old, new)


def _validated(seed):
    rng = random.Random(seed)
    for _ in range(50):
        try:
            return rng, p.validate_comparison(_phase_input(rng))
        except Exception:  # a random roster can break a validation rule; draw another
            continue
    pytest.skip('no valid roster drawn')


@pytest.mark.parametrize('seed', range(300))
def test_indexed_comparison_matches_scanning_oracle(seed):
    rng, v = _validated(seed)
    p._LAST = None
    assert p.compare(v) == oracle.compare(v)
    for phase in ('old', 'new'):
        for subject in v.subjects:
            assert p.signature(v, phase=phase, subject=subject) == oracle.signature(v, phase=phase, subject=subject)
            for scope in v.scopes:
                if scope.kind not in ('company', 'future_company'):
                    continue
                assert p.admissions(v, phase=phase, subject=subject, scope=scope) == \
                    oracle.admissions(v, phase=phase, subject=subject, scope=scope)
                for action in sorted({a.key for ph in (v.old, v.new) for a in ph.catalog.company_actions}):
                    assert p.company_action(v, phase=phase, subject=subject, scope=scope, action=action) == \
                        oracle.company_action(v, phase=phase, subject=subject, scope=scope, action=action)
                reqs = [a.requirement for a in oracle.admissions(v, phase=phase, subject=subject, scope=scope)]
                for requirement in rng.sample(reqs, min(2, len(reqs))):
                    bound = [None, *v.subjects] if subject in v.agents else [None]
                    for human in bound:
                        def ask(module):
                            try:
                                return module.execution(v, phase=phase, actor=subject, bound_human=human,
                                                        scope=scope, requirement=requirement)
                            except Exception as exc:  # the same refusal from both
                                return type(exc), str(exc)
                        assert ask(p) == ask(oracle)


def test_compare_reuses_the_answer_only_for_equal_facts():
    _, v = _validated(7)
    p._LAST = None
    first = p.compare(v)
    assert p.compare(v) is first
    # An equal input built again (the apply re-reading the preview's facts) reuses the answer.
    again = p.validate_comparison(p.ComparisonInput(v.expected, v.old, v.new))
    assert again is not v and again == v and p.compare(again) is first
    # Changed facts are compared afresh, and agree with the oracle.
    flipped = replace(v.new, visibility=tuple(replace(x, visible=False) if x.scope.kind == 'hub' else x for x in v.new.visibility))
    try:
        changed = p.validate_comparison(p.ComparisonInput(v.expected, v.old, flipped))
    except Exception:
        return
    answer = p.compare(changed)
    assert answer is not first and answer == oracle.compare(changed)


def test_errors_and_types_unchanged_on_invalid_questions():
    _, v = _validated(3)
    questions = [
        ('admissions', dict(phase='old', subject='nobody', scope=S('company', 'nope'))),
        ('admissions', dict(phase='old', subject=v.subjects[0], scope=S('hub', 'root'))),
        ('admissions', dict(phase='middle', subject=v.subjects[0], scope=S('hub', 'root'))),
        ('signature', dict(phase='new', subject='nobody')),
        ('signature', dict(phase='new', subject=3)),
    ]

    def refusal(module, name, kwargs):
        try:
            getattr(module, name)(v, **kwargs)
        except Exception as exc:
            return type(exc), str(exc), getattr(exc, '__dict__', None)
        return None

    for name, kwargs in questions:
        got = refusal(p, name, kwargs)
        assert got is not None and got == refusal(oracle, name, kwargs)
    with pytest.raises(Exception) as mine:
        p.compare(object())
    with pytest.raises(Exception) as theirs:
        oracle.compare(object())
    assert (type(mine.value), str(mine.value)) == (type(theirs.value), str(theirs.value))
