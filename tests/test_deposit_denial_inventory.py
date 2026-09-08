"""Review fence for the closed denial producers used by private deposit admission.

These AST fingerprints deliberately require a fresh closure review when a producer
or admission entrance changes; do not refresh them as routine generated output.
The full shared evidence modules include delegated scalar/batched/draft walkers,
so a new helper or denial reason cannot silently escape an exception-site list.
No user facts or permission decisions are cached or captured by this inventory.
"""
import ast
import hashlib
from pathlib import Path

from bookflow.core.registry import EXPLICIT_GRANT_ONLY_CAPABILITIES
from bookflow.hub.access import ROLE_RANK, ROLE_FOR_REQUIRED


SOURCES = {
    'company/payment_authority.py': (None, '60f79b6d63858217990ef4cef774776a92de35e4c95fc59182ecf3f9f0d595a6'),
    'company/deposit_draft_evidence.py': (None, '5175a3ae0130ac138bdcb627bbb3137c53c67edee62de44ce89e237cfb16c505'),
    'hub/access.py': (('load_memberships', 'company_role', 'role_satisfies', 'require_resource', 'require_explicit_grant'),
                      '8ed7705563acafe3a20bb26b70ddfd38b0985687ba454232bf3102804499b192'),
    'company/deposit_dependencies.py': (('authorize',), 'ee002cdbfba9390b8b461dbf27c1f0630e3d7447c73073e7c8b595bd22887a39'),
    'company/deposit_dependency_history.py': (('execution_binding', '_proven_resource_denial', '_authorize_binding_graph'),
                                             '5c820b0450f525195b8ea3f00f449d77581076b6e796a827df50fb6e19b7fcc1'),
    'company/deposit_draft_validation.py': (('admit',), '62d3ec9a506de73d45ba0429a43e50cb2a501b92fce664067a4403a9cfb5c552'),
    'company/deposit_read_authority.py': (('authenticate', 'admit'), '96702fe58a5429394e6949944404bf6be82af9d12c0ba6608f50f4664c447500'),
}


def test_denial_producer_and_admission_inventory_requires_review():
    source = Path(__file__).resolve().parents[1] / 'src/bookflow'
    for path, (names, expected) in SOURCES.items():
        tree = ast.parse((source / path).read_text())
        if names is not None:
            selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
            assert {node.name for node in selected} == set(names)
            tree = ast.Module(body=selected, type_ignores=[])
        actual = hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()
        assert actual == expected, f'Review deposit denial closure for {path}, including delegated raise sites'
    assert ROLE_RANK == {'readonly': 0, 'standard': 1, 'admin': 2, 'owner': 3}
    assert ROLE_FOR_REQUIRED == {'member': 0, 'standard': 1, 'admin': 2, 'owner': 3}
    assert EXPLICIT_GRANT_ONLY_CAPABILITIES == frozenset({
        'transaction.journal_entry.delete', 'transaction.invoice.delete',
        'transaction.sales_receipt.delete', 'transaction.payment.delete',
    })
    assert EXPLICIT_GRANT_ONLY_CAPABILITIES.isdisjoint({'ledger.read', 'ledger.post', 'customer-work'})
