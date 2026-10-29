"""Automations: rules that write with nobody asking at the time (R187 increment 2, design/principal-directives.md §3).

One rule for every kind, and no shared table: each automation is a row of its own domain (a delivery schedule, a bank
rule, later an invoice schedule) carrying the standard columns below. What this module adds is the one model:

* **Drafting is open, going live is a capability.** Anyone with the ordinary right to write the rule drafts it (state
  ``draft``: nothing runs). Making it ``live`` needs ``automate.<kind>`` (`require_establish`); the domain's own
  command does it (`delivery-schedule live`, `delivery-schedule update --mode send --confirm`, later `bank-rule update
  --live`) and records ``established_by`` (and, for an agent, ``established_for``, its principal) and
  ``established_at``.
* **A live rule runs under its establisher's authority, checked at every firing** (`firing_check`): it is live, its
  establisher is active and still holds the capability (a person: a standing membership at the threshold, not denied
  it, admitted by the activated policy; an agent: its own explicit grant, still bound to and admitted with its
  principal, who must hold it too). Otherwise the rule is ``paused`` with the reason and one to-do is raised.
* **An operative edit by a non-holder returns it to draft** (`on_edit`); by a holder it stays live under the editor.
  Pausing is open to anyone who may edit the rule (`automation pause`): stopping is safe.
* **Its writes cite the rule and the establisher:** the audit event's ``source_ref`` is
  ``automation:<kind>:<rule id>:<establisher>`` (`source_ref`), which `audit list` and `show` render as
  "On rule: <name>, established by <who>" (`render`). Every effect lands in a batch (`fire` opens one of kind
  ``automation``; a rule that acts inside another batch, R108's auto-add inside its import's, passes that one).

A kind registers a `Kind`; a timed kind (a fixture, later invoice schedules) gives ``due`` and ``act`` and is fired by
the host's tick (`tick`). R108 increment 4's bank rules add the standard columns (`columns`), register a `table_kind`,
check `require_establish` in `bank-rule update --live`, `on_edit` in `bank-rule update`, and `firing_check` (with
`pause`) at each import, citing `source_ref(..., inner=f'bank-line:{line}')` on each entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import sqlalchemy as sa

from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Touched

STATES = ('draft', 'live', 'paused')
BATCH_KIND = 'automation'
PREFIX = 'automation:'


@dataclass(frozen=True)
class Kind:
    """One kind of automation: where its rules live and how each is read, paused and (when timed) fired.

    ``view(s, row)`` gives the standard fields: rule_id, name, state, established_by, established_for, established_at,
    paused_reason, draft_reason, what (its effect, in words). ``rows(s, state)`` lists the automations (a delivery
    schedule is one only in send mode); ``get(s, selector)`` finds one by name or id (E_RECORD_NOT_FOUND);
    ``pause(s, ctx, row, why)`` writes the pause and its one to-do, returning (row after, touched)."""
    kind: str
    capability: str
    noun: str
    record_type: str
    show_command: str
    edit_command: str
    live_command: str
    rows: Callable[[Any, str | None], list[dict]]
    get: Callable[[Any, str], dict]
    view: Callable[[Any, dict], dict]
    pause: Callable[[Any, Any, dict, str], tuple[dict, list[Touched]]]
    due: Callable[[Any, Any], list[dict]] | None = None
    act: Callable[[Any, Any, dict, str], None] | None = None


KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    KINDS[kind.kind] = kind
    return kind


def kind_of(name: str) -> Kind:
    try:
        return KINDS[name]
    except KeyError:
        raise BookflowError('E_VALIDATION', message=f"No automation kind {name!r}: one of {', '.join(sorted(KINDS))}.",
                            details={'fields': [{'field': 'kind', 'problem': 'unknown kind'}]}) from None


def _batches():
    from bookflow.company import batches
    if BATCH_KIND not in batches.ADAPTERS:
        batches.register(batches.Adapter(BATCH_KIND, 'record', label='Automation run'))
    return batches


# ---------------------------------------------------------------- the standard columns (the seam for each kind)

def columns() -> list[sa.Column]:
    """The automation columns a kind's own table carries (R108 increment 4's bank rules take these)."""
    def col(name, type_, description, **kw):
        return sa.Column(name, type_, info={'description': description}, **kw)
    return [
        col('state', sa.String(8), 'draft (nothing runs), live (runs under its establisher) or paused (the reason '
            'says why).', nullable=False, server_default='draft'),
        col('established_by', sa.String(26), 'Who made it live; it runs under their authority.', nullable=True),
        col('established_for', sa.String(26), "An agent establisher's principal at the time.", nullable=True),
        col('established_at', sa.String(32), 'When it was made live.', nullable=True),
        col('paused_reason', sa.String(500), 'Why it is paused.', nullable=True),
        col('paused_at', sa.String(32), 'When it was paused.', nullable=True),
        col('draft_reason', sa.String(500), 'Why it last returned to draft from live.', nullable=True),
    ]


# ---------------------------------------------------------------- who holds the capability

def _user(s, user_id):
    from bookflow.hub import schema as h
    return s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == user_id)).mappings().first() \
        if user_id else None


def name_of(s, user_id) -> str:
    user = _user(s, user_id)
    return (user['display_name'] or user['username']) if user is not None else 'someone'


def _admits(s, user_id, principal, capability, threshold) -> bool:
    """Where permissions are activated, whether the policy in force admits ``user_id`` (with its principal, for an
    agent) for the requirement in this company. Always True in the legacy mode, which the membership rows decide."""
    from bookflow.hub import permission_catalog as pc, permission_policy as pp, permission_runtime as pr
    from bookflow.hub.permission_access import activated
    if not activated(s):
        return True
    s.hub.raw.execute('SAVEPOINT bookflow_automation_check')
    try:
        comparison = pr._operation_observation(s.hub).snapshot.comparison
        scope = pc.ScopeKey('company', s.company_row['id'])
        if user_id not in comparison.subjects or scope not in comparison.scopes:
            return False
        return pp.execution(comparison, phase='old', actor=user_id, bound_human=principal, scope=scope,
                            requirement=pc.Requirement(capability, threshold)).actor_admitted
    except Exception:  # noqa: BLE001 - authority the policy cannot vouch for does not count
        return False
    finally:
        s.hub.raw.execute('RELEASE SAVEPOINT bookflow_automation_check')


def _person_lost(s, user, capability, threshold) -> str | None:
    from bookflow.hub import capabilities
    from bookflow.hub.access import ROLE_RANK
    who = user['display_name'] or user['username']
    if not user['active']:
        return f'{who} has been deactivated'
    rows = capabilities._memberships(s, user['id'], capabilities._company_scopes(s))
    if any(capability in capabilities._policy(r)[1] for r in rows):
        return f'{who} is denied {capability} in this company'
    if not any(ROLE_RANK.get(r[2], -1) >= ROLE_RANK[threshold] for r in rows) \
            or not _admits(s, user['id'], None, capability, threshold):
        return f'{who} no longer holds {capability} in this company'
    return None


def lost(s, capability: str, user_id: str | None, principal: str | None = None) -> str | None:
    """Why ``user_id`` does not hold ``capability`` in this company now (or None): the establisher test, asked of a
    person and an agent alike. An agent holds it only by its own explicit grant, while active, unsuspended, bound to
    ``principal`` and admitted with them, and only while that principal holds it too."""
    from bookflow.hub import capabilities, credentials, schema as h
    from bookflow.hub.permission_access import activated
    threshold = capabilities.threshold(capability)
    user = _user(s, user_id)
    if user is None:
        return 'nobody established it'
    who = user['display_name'] or user['username']
    if user['kind'] == 'human':
        return _person_lost(s, user, capability, threshold)
    if user['kind'] != 'agent':
        return f'{who} cannot establish an automation'
    if not user['active']:
        return f'{who} (an agent) has been deactivated'
    suspended = s.hub.conn.execute(sa.select(h.agent_authority.c.suspended_at).where(
        h.agent_authority.c.agent_user_id == user_id)).scalar_one_or_none()
    if suspended:
        return f'{who} (an agent) is suspended'
    if not activated(s):
        return f'{who} (an agent) holds no capability until permissions are activated'
    eligible, _ = credentials._binding(s.hub, user_id, principal)
    if not principal or not eligible:
        return f'{who} (an agent) no longer acts for the person it made the rule live for'
    if not capabilities.granted(s, user_id, capability, capabilities._company_scopes(s)):
        return f'{who} (an agent) is no longer granted {capability}'
    boss = _user(s, principal)
    why = _person_lost(s, boss, capability, threshold) if boss is not None and boss['kind'] == 'human' else \
        'its principal is not a person'
    if why is not None:
        return f'{why} ({who} acts for them)'
    if not _admits(s, user_id, principal, capability, threshold):
        return f'{who} (an agent) no longer holds {capability} in this company'
    return None


def _principal(s) -> str | None:
    from bookflow.hub.capabilities import principal_of
    return principal_of(s) if s.actor is not None and s.actor.kind == 'agent' else None


def holds_now(s, capability: str) -> bool:
    """Whether the acting person or agent holds ``capability`` here now."""
    if s.actor is None:
        return False
    return lost(s, capability, s.actor.id, _principal(s)) is None


def require_establish(s, capability: str, command: str) -> None:
    """Making a rule live (or keeping it live through an operative edit) needs ``capability``. An agent is asked the
    capability gate's question first, so its refusal names the capability and its principal; then, for everyone, the
    establisher test that every firing repeats."""
    if s.actor is not None and s.actor.kind != 'human':
        from bookflow.hub import capabilities
        capabilities.require_company(s, command, capability)
    why = lost(s, capability, s.actor.id if s.actor is not None else None, _principal(s))
    if why is not None:
        raise BookflowError('E_PERMISSION', message=(
            f'Making this rule live needs the capability {capability}, and {why}. Ask your principal to grant it if '
            'this is yours to do.'), details={'capability': capability, 'reason': 'capability_not_held',
                                              'command': command, 'problem': why})


def establisher(s) -> dict:
    """The standard fields a rule made live by the acting person or agent takes."""
    return dict(state='live', established_by=s.actor.id, established_for=_principal(s), established_at=clock.now_iso(),
                paused_reason=None, paused_at=None, draft_reason=None)


# ---------------------------------------------------------------- edits, firing, pausing

def on_edit(s, capability: str, before: dict, after: dict, *, operative: bool) -> tuple[dict, list[str]]:
    """An operative edit of a live or paused rule: by a holder of ``capability`` a live rule stays live and the editor
    becomes its establisher (a paused one stays paused); by anyone else it returns to draft."""
    if not operative or before['state'] == 'draft':
        return after, []
    if holds_now(s, capability):
        if before['state'] == 'live':
            after.update(establisher(s))
        return after, []
    after.update(state='draft', established_by=None, established_for=None, established_at=None, paused_reason=None,
                 paused_at=None, draft_reason=f'changed by {name_of(s, s.actor.id)}'[:500])
    return after, [f'This change returns it to draft: it runs nothing until a holder of {capability} makes it live '
                   'again.']


def firing_check(s, k: Kind, row: dict) -> str | None:
    """Why this rule must not act now (None: it may): live, its establisher active and still holding the capability."""
    v = k.view(s, row)
    if v['state'] != 'live':
        return f"it is {v['state']}"
    return lost(s, k.capability, v['established_by'], v['established_for'])


def assignee(s, v: dict) -> str | None:
    """Who a rule's to-do goes to: its establisher if an active person, else the person an agent acted for, else a
    company administrator."""
    from bookflow.company.scheduler import _active_admin
    for user_id in (v.get('established_by'), v.get('established_for')):
        user = _user(s, user_id)
        if user is not None and user['kind'] == 'human' and user['active']:
            return user_id
    return _active_admin(s)


def raise_todo(s, ctx, k: Kind, v: dict, ref: str, title: str, notes: str) -> list[Touched]:
    """One to-do per ``ref`` (never one per firing)."""
    from bookflow.company import schema as c, todos
    who = assignee(s, v)
    if who is None:
        return []
    todo = todos.scheduler_todo(s, ctx, source_ref=ref[:512], title=title, notes=notes, assignee_id=who,
                                due_date=clock.today_iso())
    if todo is None:
        return []
    s.company.conn.execute(c.todos.insert().values(**todo))
    return [Touched('todo', todo['id'], 'create', None, 1, todo, db='company')]


def paused_todo(s, ctx, k: Kind, v: dict, why: str, at: str) -> list[Touched]:
    return raise_todo(s, ctx, k, v, f"automation-paused:{k.kind}:{v['rule_id']}:{at}",
                      f"Automation {v['name']} is paused",
                      f"{why}. It runs nothing until a holder of {k.capability} makes it live again "
                      f"({k.live_command}).")


# ---------------------------------------------------------------- citing the rule

def source_ref(kind: str, rule_id: str, established_by: str, inner: str | None = None) -> str:
    """What a rule's writes carry as their source: the rule and its establisher (and, for a rule acting on a line,
    the line: ``inner``)."""
    return (f'{PREFIX}{kind}:{rule_id}:{established_by}' + (f';{inner}' if inner else ''))[:512]


def parse(ref: str | None) -> dict | None:
    if not ref or not ref.startswith(PREFIX):
        return None
    head = ref[len(PREFIX):].split(';', 1)[0].split(':')
    if len(head) != 3:
        return None
    return dict(kind=head[0], rule_id=head[1], established_by=head[2])


def render(s, ref: str | None) -> dict | None:
    """``source_ref`` read back for `audit list`: the rule's kind, id and name, and who established it."""
    cited = parse(ref)
    if cited is None:
        return None
    name = cited['rule_id']
    k = KINDS.get(cited['kind'])
    if k is not None:
        try:
            name = k.view(s, k.get(s, cited['rule_id']))['name']
        except Exception:  # noqa: BLE001 - a rule since removed still renders by its id
            pass
    who = name_of(s, cited['established_by'])
    return dict(cited, name=name, established_by_name=who, text=f'On rule: {name}, established by {who}')


# ---------------------------------------------------------------- reading and the kind's own permission

def require_command(s, command_name: str) -> None:
    """The caller may run ``command_name`` (a kind's ``show`` to read a rule, its ``update`` to pause one)."""
    from bookflow.core import registry
    from bookflow.hub import access
    cmd = registry.get(command_name)
    for capability, role in ((cmd.capability, cmd.required_role or 'authenticated'), *cmd.resource_requirements):
        access.require_resource(s, capability, role)


def may(s, command_name: str) -> bool:
    try:
        require_command(s, command_name)
    except BookflowError as e:
        if e.code != 'E_PERMISSION':
            raise
        return False
    return True


def describe(s, k: Kind, row: dict) -> dict:
    v = k.view(s, row)
    out = dict(kind=k.kind, capability=k.capability, noun=k.noun, rule_id=v['rule_id'], name=v['name'],
               state=v['state'], what=v.get('what') or '', established_by=v['established_by'],
               established_by_name=name_of(s, v['established_by']) if v['established_by'] else None,
               established_for=v.get('established_for'), established_at=v['established_at'],
               paused_reason=v['paused_reason'], draft_reason=v.get('draft_reason'))
    if v['state'] == 'live':
        out['would_pause'] = firing_check(s, k, row)
    return out


# ---------------------------------------------------------------- tables with the standard columns

def table_kind(*, kind: str, capability: str, noun: str, record_type: str, table: sa.Table, show_command: str,
               edit_command: str, live_command: str, name_field: str = 'name', what: Callable[[dict], str] | None = None,
               due: Callable | None = None, act: Callable | None = None) -> Kind:
    """A kind whose rules are rows of ``table`` carrying `columns` and the common version columns."""
    def view(s, row):
        return dict(rule_id=row['id'], name=row[name_field], state=row['state'], established_by=row['established_by'],
                    established_for=row['established_for'], established_at=row['established_at'],
                    paused_reason=row['paused_reason'], draft_reason=row['draft_reason'],
                    what=what(row) if what else '')

    def rows(s, state=None):
        q = sa.select(table).order_by(table.c[name_field])
        if state:
            q = q.where(table.c.state == state)
        return [dict(r) for r in s.company.conn.execute(q).mappings()]

    def get(s, selector):
        found = s.company.conn.execute(sa.select(table).where(sa.or_(
            table.c.id == selector, table.c[name_field] == selector))).mappings().first()
        if found is None:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': record_type, 'selector': selector})
        return dict(found)

    def pause(s, ctx, row, why):
        if row['state'] != 'live':
            return row, []
        at = clock.now_iso()
        after = dict(row, state='paused', paused_reason=why[:500], paused_at=at, version=row['version'] + 1,
                     updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        done = s.company.conn.execute(table.update().where(table.c.id == row['id'], table.c.version == row['version'])
                                      .values(**{k: v for k, v in after.items() if k != 'id'}))
        if done.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': record_type, 'record_id': row['id']})
        k = KINDS[kind]
        return after, [Touched(record_type, row['id'], 'update', row['version'], after['version'], after, row,
                               db='company'), *paused_todo(s, ctx, k, view(s, after), why, at)]

    return Kind(kind, capability, noun, record_type, show_command, edit_command, live_command, rows, get, view, pause,
                due, act)


# ---------------------------------------------------------------- firing a timed rule on the host

def _in_tx(s, owner, fn):
    with s.commits.operation(owner, s.hub, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            out = fn()
            s.commits.commit(s.company, owner)
            return out
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


def _audit(s, ctx, command, summary, touched):
    from bookflow.core.audit import write_event_to
    if touched:
        write_event_to(s.company, ctx, command, summary, touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                       directive_code=s.directive_code)


def _pause_as_host(s, system_id, k: Kind, row: dict, why: str) -> dict:
    from bookflow.company.scheduler import _act_as, context
    _act_as(s, system_id)
    name = k.view(s, row)['name']
    ctx = context().model_copy(update={'actor_id': system_id, 'reason': f'On rule: {name}'[:140]})

    def write():
        current = k.get(s, row['id'])
        _, touched = k.pause(s, ctx, current, why)
        _audit(s, ctx, 'automation pause', f'automation {name} paused: {why}'[:512], touched)
    _in_tx(s, 'automation.pause', write)
    return {'paused': why}


def fire(s, system_id: str, k: Kind, row: dict) -> dict:
    """One firing of a live timed rule: checked first (else paused with the reason and one to-do), then its effect
    runs as its establisher, citing the rule, inside a batch of its own that one reversal undoes."""
    from bookflow.company import scheduler
    from bookflow.core.context import ActorKind
    from bookflow.hub import credentials
    v = k.view(s, row)
    why = firing_check(s, k, row)
    if why is not None:
        return _pause_as_host(s, system_id, k, row, why)
    # The establisher's authority as it stands now (firing_check asked the capability; the binding's epoch is the
    # current one, never one recorded when the rule went live: a later grant elsewhere does not pause it).
    epoch = credentials._binding(s.hub, v['established_by'], v['established_for'])[1] \
        if v['established_for'] else None
    scheduler._act_as(s, v['established_by'], v['established_for'], epoch)
    ctx = scheduler.context().model_copy(update={
        'actor_id': v['established_by'], 'actor_kind': ActorKind(s.actor.kind), 'on_behalf_of': v['established_for'],
        'reason': f"On rule: {v['name']}"[:140], 'source_ref': source_ref(k.kind, v['rule_id'], v['established_by'])})
    batch_id = _batches().open_batch(s, ctx, kind=BATCH_KIND, label=f"On rule: {v['name']}"[:200],
                                     scope=f"{k.kind}:{v['rule_id']}", source_type=k.record_type,
                                     source_id=v['rule_id'])
    try:
        k.act(s, ctx, row, batch_id)
    except BookflowError as e:
        if s.company is not None and s.company.write_transaction:
            s.company.raw.rollback()
        if e.code not in ('E_PERMISSION', 'E_UNAUTHENTICATED'):
            raise
        return dict(_pause_as_host(s, system_id, k, k.get(s, row['id']),
                                   f"{name_of(s, v['established_by'])} was refused ({e.code}): {e.message or e.code}"),
                    batch=batch_id)
    return {'batch': batch_id}


def effect(s, ctx, batch_id: str, command: str, payload: dict) -> dict:
    """Run one registry command as the rule's establisher, citing the rule (``ctx.source_ref``), and record what it
    created as effects of the rule's batch, in the same transaction as the write."""
    batches = _batches()

    def record(s2, cmd, applied, output):
        for record_type, record_id, action, version in batches.touched_effects(
                applied, {t.record_type for t in applied.touched}):
            if action == 'created':
                batches.record_effect(s2, batch_id, record_type=record_type, record_id=record_id, action=action,
                                      version_after=version, event_id=batches.last_event_id(s2))
    return batches.run_nested(s, ctx, command, payload, source_ref=ctx.source_ref, recorder=record, recovery=False)


def tick(s, system_id: str, now) -> dict | None:
    """The host's step for timed kinds: each live rule that is due fires once. None when no kind is timed."""
    timed = [k for k in KINDS.values() if k.due is not None and k.act is not None]
    if not timed:
        return None
    out = {}
    for k in timed:
        for row in k.due(s, now):
            name = k.view(s, row)['name']
            try:
                out[f'{k.kind}:{name}'] = fire(s, system_id, k, row)
            except Exception as e:  # noqa: BLE001 - recorded, never fatal to the host
                from bookflow.company.scheduler import _failure
                if s.company is not None and s.company.write_transaction:
                    s.company.raw.rollback()
                out[f'{k.kind}:{name}'] = _failure(e)
    return out or None

