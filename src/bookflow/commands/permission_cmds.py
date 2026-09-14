"""Explicit installation activation and scoped effective permission setup."""
from dataclasses import asdict
from pydantic import BaseModel, ConfigDict, Field
from bookflow.core.registry import command, Plan, Applied
from bookflow.core.errors import BookflowError
from bookflow.commands.common import WriteOutput
from bookflow.hub import identity_admin as b, permission_runtime as runtime
from bookflow.hub import permission_setup as setup, permission_policy as policy, permission_catalog as catalog
from bookflow.hub.permission_access import activated, translate


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Enrollment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_id: str = Field(description='Exact registered company lacking an administrator')
    user_id: str = Field(description='Exact active human login explicitly enrolled as this company administrator')
    expected_version: int = Field(0, ge=0, description='0 requires no exact membership; otherwise its observed version')


class ActivateInput(Empty):
    expected_generation: int = Field(ge=1, description='Generation returned by permission show')
    expected_catalog_sha256: str = Field(min_length=64,max_length=64,description='Exact executable catalog digest returned by permission show')
    administrators: list[Enrollment] = Field(default_factory=list,description='Explicit enrollment only for companies lacking an applicable human administrator')


class PermissionOutput(WriteOutput):
    mode: str
    generation: int
    catalog_sha256: str
    changed: bool = False
    companies_needing_administrator: list[dict] = Field(default_factory=list)
    affected_agents: list[str] = Field(default_factory=list)


show = command('permission show',scope='hub',capability='user',required_role='hub_admin',
    description='Inspect permission activation generation, executable catalog and exact missing company administrator coverage.',
    input_model=Empty,output_model=PermissionOutput)


def state(session):
    from bookflow.hub import permission_snapshot as snap; build = runtime.current_catalog()
    from bookflow.hub.permission_activation import _administrators
    row = session.hub.raw.execute('SELECT mode,generation FROM permission_state WHERE id=1').fetchone()
    bundle = runtime.catalog_for_root(session.hub) if row[0]=='policy_v1' else build.catalog_bundle()
    session.hub.raw.execute('SAVEPOINT permission_setup_state')
    try:
        root = snap.load_root(session.hub,catalog=bundle)
    finally:
        session.hub.raw.execute('RELEASE permission_setup_state')
    live,covered = _administrators(root)
    names = dict(session.hub.raw.execute('SELECT id,display_name FROM main.companies'))
    missing = [{'company_id':x.id,'name':names[x.id]} for x in root.companies if x.id in live-covered]
    return PermissionOutput(mode=row[0],generation=row[1],catalog_sha256=build.MANIFEST.descriptor_sha256,
        companies_needing_administrator=missing)


@show
def plan_show(inp,ctx,s):
    return Plan(state(s))


activate = command('permission activate',scope='hub',capability='user',required_role='hub_admin',writes={'hub'},
    description='Explicitly activate the reviewed per-company policy and exact named administrator enrollments. Installation status alone grants no company-book access.',
    input_model=ActivateInput,output_model=PermissionOutput,
    error_codes=['E_PERMISSION','E_VALIDATION','E_VERSION_CONFLICT'])


def preparation(inp,ctx,s,preview):
    from bookflow.hub import permission_snapshot as snap; build = runtime.current_catalog()
    current = state(s)
    if inp.expected_generation != current.generation:
        raise BookflowError('E_VERSION_CONFLICT',details={'field':'expected_generation'})
    if inp.expected_catalog_sha256 != build.MANIFEST.descriptor_sha256:
        raise BookflowError('E_VALIDATION',message='Executable catalog changed; inspect permission show again.')
    if not activated(s) or runtime.catalog_for_root(s.hub).descriptor.version == build.CATALOG.version:
        intent = b.ActivatePolicy(inp.expected_generation,inp.expected_catalog_sha256,tuple(
            b.CompanyAdministrator(x.company_id,x.user_id,b.Absent() if x.expected_version==0 else b.Version(x.expected_version)) for x in inp.administrators))
        prepared = setup.edit(s,ctx,intent,preview=preview,catalog=build.catalog_bundle())
    else:
        if inp.administrators:
            raise BookflowError('E_VALIDATION',message='Catalog upgrade retains existing administrator enrollment.')
        from bookflow.core.identity_admin_binding import session_operation
        with session_operation(s,ctx,purpose='preview'):
            old = snap.load_root(s.hub,catalog=runtime.catalog_for_root(s.hub))
        intent = b.ReplaceCatalog(old.stamp.catalog_sha256,build.catalog_bundle(),
            tuple(set(old.role_defaults)|set(build.CATALOG.defaults)))
        prepared = setup.edit(s,ctx,intent,preview=preview)
    return PermissionOutput(mode='policy_v1',generation=prepared.final.root.stamp.generation,
        catalog_sha256=build.MANIFEST.descriptor_sha256,changed=prepared.visible.changed,
        affected_agents=list(prepared.reconciliation.revoke_agents))


@activate
def plan_activate(inp,ctx,s):
    return Plan(preparation(inp,ctx,s,True),data={'input':inp})


@activate.applier
def apply_activate(plan,ctx,s):
    output = preparation(plan.data['input'],ctx,s,False)
    return Applied(output,[],'Activated per-company permissions.',audited=output.changed)


class EffectiveInput(Empty):
    company: str = Field(description='Company name or id; this scope alone is inspected')
    user: str | None = Field(None,description='Username or id; omit for yourself. Other users require company administration.')


class EffectiveOutput(BaseModel):
    can_administer: bool
    company_name: str
    company_id: str
    user_id: str
    mode: str
    permissions: list[dict]


effective = command('membership effective',scope='hub',capability='membership',
    description='Show exact effective permissions and role/grant/deny provenance in one company; self or scoped administrator.',
    input_model=EffectiveInput,output_model=EffectiveOutput)


def authorize_effective(inp,ctx,s):
    from bookflow.commands.host_cmds import resolve_scope, authorize_membership_scope
    scope = resolve_scope(s,inp.company,None)
    if inp.user is not None and inp.user not in (s.actor.id,s.actor.username):
        authorize_membership_scope(s,scope,'admin')


@effective
def plan_effective(inp,ctx,s):
    from bookflow.commands.host_cmds import resolve_scope, authorize_membership_scope, administers_scope, _membership_target
    from bookflow.hub.permission_catalog import ScopeKey
    scope = resolve_scope(s,inp.company,None)
    if inp.user is not None and inp.user not in (s.actor.id,s.actor.username):
        authorize_membership_scope(s,scope,'admin')
    user = _membership_target(s,inp.user or s.actor.id)
    extra = dict(can_administer=s.actor.kind=='human' and administers_scope(s,scope,'admin'),company_name=scope.scope_name)
    if not activated(s):
        return Plan(EffectiveOutput(**extra,company_id=scope.scope_id,user_id=user['id'],mode='legacy',permissions=[]))
    observed = runtime.observe_current(s.hub).snapshot
    rows = policy.admissions(observed.comparison,phase='old',subject=user['id'],scope=ScopeKey('company',scope.scope_id))
    return Plan(EffectiveOutput(**extra,company_id=scope.scope_id,user_id=user['id'],mode='policy_v1',
        permissions=[asdict(row) for row in rows]))


plan_effective.authorize_input = authorize_effective
