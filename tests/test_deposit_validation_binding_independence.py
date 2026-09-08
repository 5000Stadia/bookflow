"""Bounded precursor: identical stored values under admitted real bindings.

No validator extraction or permission mock. The ONLY excluded return component
is deposit_drafts.load()[3], its documented execution binding, asserted by identity
below. Full operation/consumption/aggregate values (including ReadEvidence) compare
without normalization, field removal, serialization or audience redaction.
"""
import ast
import hashlib
from pathlib import Path
from contextlib import contextmanager

import pytest

from bookflow import BookflowError
from bookflow.adapters.http.app import Credential
from bookflow.adapters.http.execution import _reader_binding
from bookflow.company import deposit_drafts as drafts, deposit_selection as selections
from bookflow.company import deposit_operation_pages as opages, deposit_draft_consumption as consumption
from bookflow.company import deposit_read_facts as facts, deposit_dependency_history as history
from bookflow.company import deposit_coordination as coordinate, deposit_coordinate_persistence as cp
from bookflow.company import deposit_draft_models as m
from bookflow.company.deposit_coordinate_models import CoordinateInput
from bookflow.core import identity_admin_binding as binding
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub.audit_projection import HistorySelection
from bookflow.hub import credentials
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.storage.engine import open_database
from tests.conftest import make_actor
from tests.permission_admin_support import insert, OLD
from tests.test_permission_snapshots import install_fixture_policy
from tests.test_deposit_draft_financial import run_private, make_posted
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path


@pytest.fixture
def current_policy(root):
    with open_database(root/'hub.db', writable=True) as db:
        install_fixture_policy(db.raw, catalog_bundle())


@pytest.fixture
def state(current_policy, root, client, cash, run_private):
    posted, original = make_posted(client, cash, run_private)
    edit = run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(
        from_deposit=posted.current.id,expected_version=1),'create'))
    child = run_private(lambda s,ctx:selections.run(s,ctx,m.SelectionCreate(
        draft=edit.id,expected_version=edit.version),'create'))
    accepted = run_private(lambda s,ctx:selections.run(s,ctx,m.SelectionAccept(
        selection=child.id,expected_version=child.version,draft=edit.id,expected_draft_version=edit.version),'accept'))
    edit = accepted.draft
    assert accepted.selection.accepted_revision_id==edit.revision_id
    inp = CoordinateInput.model_validate(dict(deposit=posted.current.id,expected_version=1,
        operation_key='binding-coordinate',source_action=dict(kind='sales_receipt_update',input=dict(
            sales_receipt=cash['source'],expected_version=2,memo='Binding-independent coordinate')),
        replacement=dict(mode='document',document=dict(mode='draft',draft=edit.id,expected_version=edit.version),
            draft_source_result='retain')))
    def execute(s,ctx):
        credential = OSBinding.from_session(s)
        prepared = coordinate.prepare(s,ctx,inp,binding=credential)
        prepared = coordinate.prepare(s,ctx,inp.model_copy(update={'dependency_guard':prepared.dependency_guard}),binding=credential)
        return cp.execute(s,ctx,prepared)
    coordinated = run_private(execute)
    assert coordinated.current_draft.state=='consumed' and coordinated.current_draft.id==edit.id
    assert coordinated.current.revision_bank_total==6000
    cid=client.company.show(company=COMPANY)['company_id']
    owner=Config.load(root/'config.toml').user_table(os_login())['user_id']
    users={name:make_actor(root,'deposit-binding-'+name,company_role=(cid,role))
        for name,role in [('first','standard'),('second','readonly')]}
    users['agent']=make_actor(root,'deposit-binding-agent',company_role=(cid,'standard'),kind='agent',owner_user_id=owner)
    users['denied']=make_actor(root,'deposit-binding-denied')
    creds={}
    with open_database(root/'hub.db',writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        insert(db.raw,'agent_authority',dict(agent_user_id=users['agent'],epoch=1,version=1,
            authorized_at=OLD,authorized_by=owner,permitted_use_at=OLD))
        insert(db.raw,'agent_principals',dict(agent_user_id=users['agent'],principal_user_id=users['second'],
            assigned_by=owner,assigned_at=OLD))
        for name,uid in users.items():
            principal=users['second'] if name=='agent' else None
            row,secret=credentials.issue_token(db,user_id=uid,kind='bearer',label='Owned deposit precursor',
                days=None,via='python',actor_id=owner,on_behalf_of=principal)
            creds[name]=Credential(uid,row['id'],'bearer',None,on_behalf_of=principal,
                actor_kind='agent' if name=='agent' else 'human',secret=secret)
        db.raw.execute('COMMIT')
    path=database_path(client)
    host=Host(root,version=client_version());host.start()
    try:
        yield dict(root=root,host=host,creds=creds,users=users,company=cid,path=path,
            deposit=posted.current.id,operations=(posted.operation_id,coordinated.operation_id),
            drafts=(original.id,edit.id),selection=accepted.selection.id,source=cash['source'])
    finally:host.stop()


@contextmanager
def observed(state,who):
    ctx=Context.new('http','Deposit binding precursor').model_copy(update={'request_id':'REQUEST'})
    cred=state['creds'][who]
    selected=HistorySelection(mode='list',company=state['company'])
    with binding.hosted_reader(state['host'],_reader_binding(state['host'],cred,ctx.request_id),request_id=ctx.request_id) as reader:
        identity=reader.authenticate()
        assert (identity.actor,identity.actor_kind,identity.principal,identity.epoch)==(
            state['users'][who],'agent' if who=='agent' else 'human',state['users']['second'] if who=='agent' else None,
            1 if who=='agent' else None)
        assert not identity.hub_admin
        open_selected(reader,selected,ctx)
        yield reader.session,cred


def stored(state):
    with open_database(state['path'],writable=False) as db:
        company=tuple(db.raw.iterdump())
    hub=state['host'].submit(lambda:tuple(state['host']._hub.raw.iterdump()))
    return hub,company


def test_same_state_financial_loaders_for_admitted_bindings(state,record_property):
    before=stored(state);values=[];identities=[]
    for who in ('first','second','agent'):
        with observed(state,who) as (s,cred):
            identity=history.execution_binding(s,cred);identities.append(identity)
            original=[];consumed=[];loaded=[]
            for operation,draft in zip(state['operations'],state['drafts']):
                saved=drafts._row(s,opages.c.deposit_operations,operation)
                output=opages.authorized_original(s,saved,cred,write=False)
                assert output.operation_id==operation and output.current.id==state['deposit']
                original.append(output)
                current=consumption.current(s,operation,binding=cred,write=False)
                assert current is not None and current.id==draft and current.operation_id==operation and current.state=='consumed'
                consumed.append(current)
            assert original[1].command=='deposit coordinate'
            assert set(original[1].effect.target_ids)=={state['deposit'],state['source']}
            for kind,identifier in [('draft',x) for x in state['drafts']]+[('selection',state['selection'])]:
                header,revision,manifest,returned_binding=drafts.load(s,identifier,kind=kind,binding=cred)
                assert returned_binding is cred
                assert history.execution_binding(s,returned_binding)==identity
                assert header['id']==identifier and manifest.summary.source_count==1
                assert header['state']==('consumed' if kind=='draft' else 'accepted')
                loaded.append((header,revision,manifest))
            aggregate=facts.load_complete(s,[state['deposit']],binding=cred)
            assert len(aggregate)==1 and aggregate[0].header['id']==state['deposit']
            assert len(aggregate[0].selected)==2
            assert all(effect.bank_total==6000 for effect in aggregate[0].effects.values())
            assert len(aggregate[0].evidence.consumed_links)==2
            assert any(link.kind=='selection' and link.id==state['selection'] for link in aggregate[0].links)
            values.append((tuple(original),tuple(consumed),tuple(loaded),aggregate))
        assert stored(state)==before and state['host']._readers_attached==0
    assert len({identity[0] for identity in identities})==3
    assert identities[2][2]==identities[1][0] and identities[2][3]==1
    # Exact native equality includes every financial, history and evidence field.
    assert values[0]==values[1]==values[2]
    record_property('binding_identities',repr(identities))
    record_property('coverage','2 operations (post+coordinate), 2 consumed drafts, 1 accepted selection, complete 2-revision deposit; bank total6000')
    # Real no-membership credential is refused at selected-company admission.
    with pytest.raises(BookflowError) as denied:
        with observed(state,'denied'):pytest.fail('Denied company admitted')
    assert denied.value.code in ('E_PERMISSION','E_COMPANY_NOT_FOUND')
    record_property('denied_company_code',denied.value.code)
    # Existing output is not a proof allowing a different execution binding.
    with observed(state,'first') as (s,cred):
        wrong=state['creds']['second'];saved=drafts._row(s,opages.c.deposit_operations,state['operations'][0])
        for load in (
            lambda:opages.authorized_original(s,saved,wrong),
            lambda:consumption.current(s,state['operations'][0],binding=wrong),
            lambda:drafts.load(s,state['drafts'][0],binding=wrong),
            lambda:drafts.load(s,state['selection'],kind='selection',binding=wrong),
            lambda:facts.load_complete(s,[state['deposit']],binding=wrong),
        ):
            with pytest.raises(BookflowError) as caught:load()
            assert caught.value.code=='E_UNAUTHENTICATED'
    assert stored(state)==before and state['host']._readers_attached==0


# Source assessment: staged derivation extraction after accepted precursor 9d214d30.
# Financial functions now retain only CompanyFacts(company.conn). Wrappers retain
# all admission and credential returns. OriginalDecode/require_original are pure
# stored-value decoding/diagnosis. The moved bank validator calls precisely
# deposit_read_authority.select (data-only); no admission-module-wide exemption.
# DraftHistoryProof and History now receive CompanyFacts in derivation. Their
# selected unchanged delegates and the entire new module are pinned below.
# Consumption error normalization stays around its moved raw validator.
# This is a finite
# change detector anchored in review, NOT a taint analyzer or a proof for every
# graph/permission model. Never refresh a digest without reviewing the changed
# body, its new calls/aliases and this assessment, and retaining the real-binding
# witness above. Stored writer attribution stays in the compared payload.
#
# Four owners: opages authenticates/admit indexed and recovered roots, then
# returns the stored decoded output unchanged; consumption.current(nonempty)
# admits through drafts.load and builds its state from stored header/revision;
# drafts.load admits before decoding and returns stored header/revision/manifest
# PLUS its binding in position 4; facts.load_complete admits the complete graph,
# delegates to those loaders and assembles financial/history/evidence values.
# It discards the fourth draft component, never copies it into ReadEvidence.
#
# Session flows through SQL selectors, DraftHistoryProof.self.s and History.self.s.
# These read company-owned rows; company identity/currency are stored inputs.
# The ONLY reviewed current-reader identity uses in this selected deposit flow
# are execution_binding (credential revalidation, Session actor/root agreement,
# principal/epoch), v.admit (OS fallback, ctx/principal agreement, memberships),
# and _authorize_binding_graph (actor+principal replaced Session views for gates).
# Their view/actor/principal aliases are included as full bodies. No financial
# shaping by current actor, timezone, principal or memberships was found.
# ReadEvidence and HistoryEntry actor fields are stored evidence, not exclusions.
#
# Below are the bounded derivation delegates, including Session-carrying helpers
# and pure stored-value transforms. Each selection also freezes ALL module-level
# statements other than unselected def/class declarations: imports, aliases,
# registries and rebinding cannot silently change the selected body meaning.
# Full nested bodies catch new aliases/getattr/innocuously named helpers at the
# call site, independent of spelling; adding an unused function is not a failure.
#
# Trust boundaries, not universal closure: Python/SQLAlchemy/Pydantic, schema and
# model validators, Session/connection behavior, shared credential/access owners,
# feature admission, declaration conformance, and generic source-master snapshot
# codecs are not exhaustively pinned here. The shared admission/evidence closure
# has its own test_deposit_denial_inventory fence. History's source-master image
# dispatch is pinned but the items/parties/pricing/units/custom-field libraries
# it calls are not. Payment/tax/custom/corrupt branches have reviewed stored-input
# call shapes, NOT new behavioral coverage. The live witness covers receipt post,
# coordinate, consumed drafts and accepted selection under current read-equivalent
# roles. New ambient identity behavior beyond this named perimeter, monkeypatches,
# and future policy/catalog semantics require separate review and witnesses.
#
# Named reviewed selections, not a general module/table inventory.
FLOW_OWNERS = {
    'deposit_financial_derivation': ('CompanyConnection', 'CompanyFacts', 'checked', '_row', 'DraftStart', 'RevisionStart', 'RootFinancial', 'begin_draft', 'begin_revision', 'finish_revision', 'require_row_origins', '_validate_consumed', 'require_consumed', 'require_no_consumption', 'consumed_state', 'derive_root', '_bank', 'require_operation_items', 'require_consumption_match'),
    'deposit_operation_pages': ('authorized_original', 'collections', 'typed_collections', 'OriginalDecode', 'decode_original', 'require_original'),
    'deposit_operations': ('decode_output',),
    'deposit_draft_consumption': ('current', '_validate_consumed', 'validate_consumed'),
    'deposit_drafts': ('load', '_row'),
    'deposit_draft_validation': ('admit', 'require', 'summary', 'validate_manifest', 'decode_revision'),
    'deposit_draft_history': ('require', 'HeaderEndpoint', 'DraftHistoryProof'),
    'deposit_draft_provider': ('validate_row_origins',),
    'deposit_read_facts': ('ValidatedDeposit', 'invalid', 'load_complete', '_history', '_navigation', '_associations'),
    'deposit_read_authority': ('ReadEvidence', 'select', 'authenticate', 'admit'),
    'deposit_sources': ('require', 'graph_many', '_partition', 'CashPartition', '_project', 'project'),
    'deposit_read_validation': ('require', 'exact_one', 'audit_rows', 'revision', 'posting_lifecycle', 'lifecycle'),
    'deposit_validation': ('require', 'validate_intent', 'validate'),
    'deposit_dependency_history': ('execution_binding', '_proven_resource_denial', '_authorize_binding_graph',
        'canonical', 'Owner', 'MissingHistory', '_audit_image', '_decoded', '_project', 'History',
        '_source_owner_image', '_complete_unit_image', '_unit_matches', '_source_raw_child', '_source_image_types'),
    'document_effects': ('rows',),
    'payment_queries': ('canonical', 'digest'),
    'sales': ('profile_row', 'saved_lines', '_custom_semantic', '_line_semantic', '_saved_semantic'),
    'tax_attribution': ('semantic_profile',),
}

# Admission calls AND binding-carrying loaders are allowed explicitly, with exact
# argument flow; this does not allow arbitrary calls merely named "authorize".
BINDING_CALLS = {
    ('deposit_read_authority', 'authenticate'): (
        'draft_admit(s, binding=binding, write=False)',
    ),
    ('deposit_read_authority', 'admit'): (
        'authenticate(s, binding)',
        'h._authorize_binding_graph(s, binding, tuple(sorted(group)), write=False)',
        "draft_admit(s, binding=binding, draft=draft['id'], write=False)",
        'h._authorize_binding_graph(s, binding, tuple(sorted(roots)), tuple(sorted(events)), write=False)',
        'draft_admit(s, binding=binding, draft=identity, write=False)',
    ),
    ('deposit_operation_pages', 'authorized_original'): (
        'history.execution_binding(s, binding)',
        'history._authorize_binding_graph(s, binding, tuple(sorted(set(ids))), write=write)',
    ),
    ('deposit_draft_consumption', 'current'): (
        "drafts.load(s, rows[0]['draft_id'], ctx=ctx, binding=binding, write=write)",
    ),
    ('deposit_drafts', 'load'): (
        'v.admit(s, ctx, binding, **{kind: identity}, write=write)',
    ),
    ('deposit_read_facts', 'load_complete'): (
        'authority.authenticate(s, binding)',
        'authority.admit(s, ids, binding=binding)',
        'h._authorize_binding_graph(s, binding, tuple(sorted(roots)), tuple(sorted(events)), write=False)',
        'deposit_drafts.load(s, identity, kind=kind, binding=binding)',
        'opages.authorized_original(s, op, binding, write=False)',
        "deposit_draft_consumption.current(s, op['id'], binding=binding, write=False)",
    ),
}
BINDING_RETURN = ('deposit_drafts', 'load', 'return (header, dict(revision), manifest, binding)')

# Filled once from the reviewed selections above. No update/generate mode exists
# in the tests; an AST change requires a new source-flow assessment.
FLOW_DIGESTS = {
    'deposit_financial_derivation': '200ad5050ab32abf1fe6d2842f5f594cf8f736fdf175b5f2df0dd9bec763cf13',
    'deposit_operation_pages': '598b25b6bfcc974957e88b3786b1c9d3c6ace895cdf513a743fc31b7867c4d46',
    'deposit_operations': '1d0bf86ac1cddc8fb8453e03d26353b1878e6265f7a9fb14d546cb3fc07ba903',
    'deposit_draft_consumption': 'c5784f641df128518c9e02dfcee1e1ce50a7b1054b50d03750728e4bf995025d',
    'deposit_drafts': 'a23aec5ee36f84047923c530e4b238935f9dae6e78ab0fe95ffcef92783b0fc8',
    'deposit_draft_validation': 'a55c0f68b9f6f1350fb5920dcced913a9f16300a490f29ff2a95828aa4ba89d7',
    'deposit_draft_history': '73169adbc158396acca700937a5fe0c1220e5aa46834e81505afbacf70f1f0ee',
    'deposit_draft_provider': '81c88b8bf62391588eaabf31840cfc3e8793bd64c2b339d3be816c6ec5839020',
    'deposit_read_facts': '4cd2155f4ace68977b8efdec54d605166c3c92ab9d04d73a563f5713a9332b5a',
    'deposit_read_authority': '65c398b3b754f876f6dfdaa8fc1ba3a780261b8ca76a960fa00137b1e09558bf',
    'deposit_sources': '6f3b3dc473e6a3df8fde53f0c58d1ba31786788a8b474dfa1fb1b6d264fcc7a0',
    'deposit_read_validation': '02ea40e755ad4f8145eff6acb02cf6d0265a2b064a67f0dca1346208f0583089',
    'deposit_validation': '5a5246068c1feebf5a888dd25fe91169105cae0566cdf6ee1028a94c8403e67a',
    'deposit_dependency_history': 'bd7efcf091a1f07cc8461480acfa0ea27b0f9511dba4fb08e9d25ccd3ce2408a',
    'document_effects': '559f13d0f1be74746f5af60676404eeb3ae611a5a2f4dd94e1e8dd47434b7d7b',
    'payment_queries': 'cd81aeb149993625bf9f102b06cc160a18914641c55dff94ea586d52c5be5691',
    'sales': 'e70fa06295a2b3300840ff38db222f3a8ef9cfdf57f64f3e416a0314477f512c',
    'tax_attribution': 'b6b47c84ab3dc2faa7529a2b4649d9b34a8f110e0673072eb79a3e19eaf51b97',
}


def _flow_sources():
    source = Path(__file__).resolve().parents[1] / 'src/bookflow/company'
    return {name: ast.parse((source / (name + '.py')).read_text()) for name in FLOW_OWNERS}


def _flow_selection(tree, names):
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    selected = [node for node in tree.body if isinstance(node, definitions) and node.name in names]
    assert sorted(node.name for node in selected) == sorted(names), 'Missing/duplicate reviewed owner'
    return ast.Module(body=[node for node in tree.body
        if not isinstance(node, definitions) or node.name in names], type_ignores=[])


def _assert_binding_flow(sources):
    assert set(FLOW_DIGESTS) == set(FLOW_OWNERS) == set(sources)
    for name, names in FLOW_OWNERS.items():
        tree = _flow_selection(sources[name], names)
        actual = hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()
        assert actual == FLOW_DIGESTS[name], (
            f'Review binding source flow: {name}; inspect aliases and reachable delegates, not just refresh hash')
    for (module, owner), allowed in BINDING_CALLS.items():
        function = next(node for node in sources[module].body if isinstance(node, ast.FunctionDef) and node.name == owner)
        parents = {child: node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
        uses = [node for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id == 'binding' and isinstance(node.ctx, ast.Load)]
        calls = []
        returns = []
        for use in uses:
            node = use
            while node in parents and not isinstance(node, (ast.Call, ast.Return)):
                node = parents[node]
            if isinstance(node, ast.Call):
                calls.append(ast.unparse(node))
            elif isinstance(node, ast.Return):
                returns.append((module, owner, ast.unparse(node)))
            else:
                pytest.fail(f'Unreviewed binding use: {module}.{owner}: {ast.unparse(node)}')
        assert sorted(calls) == sorted(allowed), (module, owner, calls)
        assert returns == ([BINDING_RETURN] if (module, owner) == BINDING_RETURN[:2] else [])


def test_reviewed_binding_source_flow():
    _assert_binding_flow(_flow_sources())


@pytest.mark.parametrize('module,owner,statements', [
    # Direct binding alias used as an innocuously named derived output value.
    ('deposit_operation_pages', 'authorized_original',
        "carrier = binding\nmetric = carrier.user_id\noutput = output.model_copy(update={'operation_id': metric})"),
    # Session alias, with no reference to the identifier 'binding', changes output.
    ('deposit_drafts', 'load',
        "session = s\nchannel = session.actor\nrevision = dict(revision, memo=channel.id)"),
    # A reachable derivation delegate also carries identity without a binding arg.
    ('deposit_drafts', '_row',
        "channel = s\nmetric = channel.actor.id\nreturn dict(value, memo=metric)"),
])
def test_source_flow_rejects_identity_derived_alias(module, owner, statements):
    sources = _flow_sources()
    _assert_binding_flow(sources)  # Establish that the failure is the mutation.
    function = next(node for node in sources[module].body if isinstance(node, ast.FunctionDef) and node.name == owner)
    assert isinstance(function.body[-1], ast.Return)
    injected = ast.parse(statements).body
    if isinstance(injected[-1], ast.Return):
        function.body[-1:] = injected
    else:
        function.body[-1:-1] = injected
    ast.fix_missing_locations(sources[module])
    # Mutate parsed source only: never import/execute a mutant or touch runtime.
    # This proves change detection at these flow sites, not semantic taint proof.
    with pytest.raises(AssertionError, match=f'Review binding source flow: {module};'):
        _assert_binding_flow(sources)


@pytest.mark.parametrize('statement,exception,match', [
    ('carrier = binding', pytest.fail.Exception, 'Unreviewed binding use:'),
    ('output = output.model_copy(update={"operation_id": binding.user_id})',
        AssertionError, 'authorized_original'),
    ('return binding', AssertionError, None),
])
def test_binding_flow_independent_of_fingerprint(monkeypatch, statement, exception, match):
    sources = _flow_sources()
    _assert_binding_flow(sources)
    module = 'deposit_operation_pages'
    function = next(node for node in sources[module].body
        if isinstance(node, ast.FunctionDef) and node.name == 'authorized_original')
    injected = ast.parse(statement).body
    if isinstance(injected[-1], ast.Return):
        function.body[-1:] = injected
    else:
        function.body[-1:-1] = injected
    ast.fix_missing_locations(sources[module])
    # Bypass only this parsed mutant's fingerprint to exercise the flow layer.
    # This temporary in-memory substitution is not a source-hash update mode.
    digest = hashlib.sha256(ast.dump(
        _flow_selection(sources[module], FLOW_OWNERS[module]),
        include_attributes=False).encode()).hexdigest()
    monkeypatch.setitem(FLOW_DIGESTS, module, digest)
    with pytest.raises(exception, match=match) as caught:
        _assert_binding_flow(sources)
    assert 'Review binding source flow:' not in str(caught.value)


def test_binding_flow_checks_later_owner_return(monkeypatch):
    sources = _flow_sources()
    _assert_binding_flow(sources)
    module = 'deposit_drafts'
    function = next(node for node in sources[module].body
        if isinstance(node, ast.FunctionDef) and node.name == 'load')
    # The legitimate fourth binding return must not permit another position.
    function.body[-1:] = ast.parse('return binding, header, revision, manifest').body
    ast.fix_missing_locations(sources[module])
    digest = hashlib.sha256(ast.dump(
        _flow_selection(sources[module], FLOW_OWNERS[module]),
        include_attributes=False).encode()).hexdigest()
    monkeypatch.setitem(FLOW_DIGESTS, module, digest)
    with pytest.raises(AssertionError) as caught:
        _assert_binding_flow(sources)
    assert 'Review binding source flow:' not in str(caught.value)
