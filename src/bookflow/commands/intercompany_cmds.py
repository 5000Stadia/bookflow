"""Both-authorized relationship setup. Staged local mappings precede hub registration."""
from bookflow.company import intercompany as owner, intercompany_relationship_models as m
from bookflow.core.registry import command

_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_COMPANY_NOT_FOUND', 'E_PERMISSION',
           'E_FEATURE_DISABLED', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED']


def plan_mapping(inp, ctx, s):
    return owner.plan_mapping(inp, ctx, s)


def plan_create(inp, ctx, s):
    return owner.plan_relationship(inp, ctx, s)


def plan_update(inp, ctx, s):
    return owner.plan_relationship(inp, ctx, s)


def plan_show(inp, ctx, s):
    return owner.plan_show(inp, ctx, s)


def plan_list(inp, ctx, s):
    return owner.plan_list(inp, ctx, s)


mapping_set = command('intercompany-mapping set', scope='company',
    description='Save an immutable local due-to/due-from mapping revision for an authorized counterpart. Requires management in both companies. Accounts must be unused and home-currency. This reserves only local accounts; it does not activate a relationship or post money. Configure each company, then intercompany-relationship create.',
    input_model=m.MappingSet, output_model=m.Mapping, writes={'company'}, required_role='admin',
    capability='intercompany.manage', accepts_idempotency_key=True, error_codes=_ERRORS)(plan_mapping)
mapping_set.authorize_input = owner.authorize_mapping
mapping_set.plan_in_write = True
mapping_set.applier(owner.apply_mapping)


def _relationship(name, inp, planner):
    cmd = command(name, scope='company', description='Register or revise the relationship using both saved local mappings. Both companies must be authorized, enabled, in the same organization and home currency. No ledger effects; future posting is a separate command. A disabled relationship retains its identity and history.',
        input_model=inp, output_model=m.Relationship, writes={'hub'}, required_role='admin',
        capability='intercompany.manage', accepts_idempotency_key=True, error_codes=_ERRORS)(planner)
    cmd.authorize_input = owner.authorize_relationship
    cmd.applier(owner.apply_relationship)
    return cmd


relationship_create = _relationship('intercompany-relationship create', m.Peer, plan_create)
relationship_update = _relationship('intercompany-relationship update', m.RelationshipUpdate, plan_update)
relationship_show = command('intercompany-relationship show', scope='company', description='Show a relationship only with current read authority in both companies; unavailable counterparts are indistinguishable from unknown ones.',
    input_model=m.RelationshipSelect, output_model=m.Relationship, writes=set(), required_role='member',
    capability='intercompany.read', error_codes=_ERRORS)(plan_show)
relationship_show.authorize_input = owner.authorize_read
relationship_list = command('intercompany-relationship list', scope='company', description='List only relationships readable in both companies, filtered before pagination. A company-only reader receives no counterpart names, counts or setup status.',
    input_model=m.RelationshipList, output_model=m.RelationshipPage, writes=set(), required_role='member',
    capability='intercompany.read', error_codes=_ERRORS)(plan_list)
relationship_list.resource_requirements = (('ledger.read', 'member'),)
