"""Reconcile existing registry lifecycle writes on their caller-owned hub writer."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from functools import wraps
from bookflow.core.registry import Touched
from bookflow.core.session import now_iso
from . import permission_snapshot as snap, permission_runtime as runtime
from . import identity_admin as admin, agent_authority as agents
from .permission_admin_audit import read_state
from .permission_access import activated, translate


@contextmanager
def scope_change(session, via):
    if not activated(session) or getattr(session,'_permission_scope_depth',0):
        yield []
        return
    if not session.hub.write_transaction:
        raise RuntimeError('Registry policy reconciliation requires its existing writer')
    session._permission_scope_depth = 1
    effects = []
    try:
        bundle = runtime.catalog_for_root(session.hub)
        old = snap.load_root(session.hub,catalog=bundle)
        old_state = read_state(session.hub)
        old_tokens = agents.read_tokens(session.hub)
        yield effects
        current = snap.load_root(session.hub,catalog=bundle)
        if current == old:
            return
        if (current.users,current.assignments,current.authorities,current.role_defaults,current.stamp.generation) != (
                old.users,old.assignments,old.authorities,old.role_defaults,old.stamp.generation):
            raise RuntimeError('Unexpected authority writer inside registry lifecycle')
        def changed(before, after):
            previous = {x.id:x for x in before}
            return tuple(x for x in after if previous.get(x.id)!=x)
        scopes = snap.ScopeRows(changed(old.organizations,current.organizations),changed(old.companies,current.companies),
            tuple(sorted(set(old.keys.organizations)-set(current.keys.organizations))),
            tuple(sorted({x.id for x in old.companies}-{x.id for x in current.companies})),
            tuple(sorted({x.id for x in old.memberships}-{x.id for x in current.memberships})))
        changes = snap.ProposalRows(memberships=changed(old.memberships,current.memberships))
        generation = agents.increment(old.stamp.generation,'generation')
        initiating = snap.derive_scope_proposal(old,old_catalog=bundle,new_catalog=bundle,scopes=scopes,
            changes=changes,generation=generation)
        # Independent SQL observation must exactly match the declared structural projection.
        if replace(initiating.root,stamp=current.stamp) != current:
            raise RuntimeError('Registry facts differ from declared scope projection')
        pair = snap.assemble_pair(old,initiating.root,old_catalog=bundle,new_catalog=bundle,visibility=runtime.VISIBILITY)
        at = now_iso()
        result = agents.reconcile(old,initiating.root,pair=pair,actor_id=session.actor.id,at=at,via=via)
        final = snap.derive_scope_proposal(old,old_catalog=bundle,new_catalog=bundle,scopes=scopes,
            changes=replace(changes,authorities=result.authorities),generation=generation)
        final_tokens = agents.revoke_tokens(old_tokens,result.revoke_agents,actor_id=session.actor.id,at=at,via=via)
        new_state = replace(old_state,generation=generation,updated_at=at,updated_by=session.actor.id,updated_via=via)
        for table,kind,before_rows,after_rows,key in (
            ('agent_authority','agent_authority',old.authorities,final.root.authorities,'agent_user_id'),
            ('api_tokens','api_token',old_tokens,final_tokens,'id'),
            ('permission_state','permission_state',(old_state,),(new_state,),'id')):
            previous = {getattr(x,key):x for x in before_rows}
            for row in after_rows:
                before = previous[getattr(row,key)]
                if row == before:
                    continue
                admin._write_mutation(session.hub,admin._mutation(table,before,row,(key,)))
                effects.append(Touched(kind,str(getattr(row,key)),'update',
                    getattr(before,'version',old.stamp.generation),getattr(row,'version',generation),
                    asdict(row),before=asdict(before)))
        if snap.load_root(session.hub,catalog=bundle) != final.root:
            raise RuntimeError('Registry reconciliation final facts mismatch')
        from .access import load_memberships
        load_memberships(session)
    except admin.AdministrationError as exc:
        translate(exc)
    finally:
        session._permission_scope_depth = 0


def registry_write(fn):
    """Shared registry owners include explicit removal and pending-trash recovery."""
    @wraps(fn)
    def wrapped(session,*args,**kwargs):
        ctx = getattr(session,'_permission_context',None)
        via = kwargs.get('via',ctx.interface.value if ctx else 'python')
        with scope_change(session,via) as effects:
            result = fn(session,*args,**kwargs)
        if isinstance(result,list):
            result.extend(effects)
        elif isinstance(result,tuple) and isinstance(result[-1],list):
            result[-1].extend(effects)
        else:
            session.hub_touched.extend(effects)
        return result
    return wrapped
