"""Actual history proofs must fit the MCP bounded receipt value graph."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from bookflow.core import registry
from bookflow.core.history_cursors import reader_digest
from bookflow.core.context import Context
from bookflow.core.publication import PublicationPermit
from bookflow.adapters.http.execution import run_hosted
from bookflow.adapters.mcp.intents import retained_size
from tests.test_history_cursors import world
from tests.test_audit_projection_publication import credential


def test_executed_history_permit_can_be_accounted_and_restored(world):
    registry.load_all()
    host, *_ = world
    ctx = Context.new('mcp', 'Read business history').model_copy(update={'request_id': 'REQUEST'})
    result = run_hosted(host, registry.get('hub audit list'), {'limit': 1},
                        ctx, credential(host, 'R'), None, 'none', False)
    identity = result.permit.audit_proof.identity
    assert type(identity.root) is str
    old_identity = replace(identity, root=Path(identity.root))
    assert reader_digest(SimpleNamespace(identity=identity)) == reader_digest(SimpleNamespace(identity=old_identity))
    state = result.permit.retained()
    assert retained_size(state) > 0
    restored = PublicationPermit.from_retained(state)
    assert restored.audit_proof.matches(result)
    assert restored.audit_proof.identity == result.permit.audit_proof.identity


class CustomPathlike:
    def __fspath__(self):
        raise AssertionError('Retained accounting must not invoke filesystem coercion')


@pytest.mark.parametrize('value', [object(), CustomPathlike(), Path('/unused/synthetic')])
def test_retained_graph_still_rejects_opaque_objects(value):
    with pytest.raises(TypeError, match='explicit value graph'):
        retained_size({'opaque': value})
