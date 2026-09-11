import ast
import json
from pathlib import Path

from tests.mcp_coverage import execution_map


def resolves(witness):
    """The named test function exists in the named file, or the row is a claim about nothing."""
    filename, name = witness.split('::')
    module = ast.parse(Path(filename).read_text())
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
               for node in module.body)


def test_registry_execution_ledger_has_no_unclassified_commands(tmp_path):
    rows = execution_map()
    assert len(rows) == 388
    assert sum(row["coverage"] == "four_surface_scenario" for row in rows) == 383
    assert sum(row['coverage'] == 'local_lifecycle_scenario' for row in rows) == 5
    assert all(row['execution_witness'] and not row['coverage'].startswith('pending') for row in rows)
    assert all(row['local_valid_witnesses'] for row in rows if row['coverage'] == 'local_lifecycle_scenario')
    assert {row['mode'] for row in rows} == {'routed_json', 'advisory', 'binary_input', 'binary_output', 'local_lifecycle', 'standalone_local', 'standalone_protocol', 'finite_poll_with_local_follow'}
    # A witness that does not exist is the failure this ledger exists to catch, so
    # every name it cites has to resolve to a real test before the count means anything.
    for row in rows:
        assert resolves(row['execution_witness']), (row['command'], row['execution_witness'])
        for witness in row['local_valid_witnesses']:
            assert resolves(witness), (row['command'], witness)
    (tmp_path / 'execution-coverage.json').write_text(json.dumps(rows, indent=2))


def test_local_workbench_rows_link_actual_lifecycles_without_claiming_browser_routes():
    from tests.mcp_coverage import local_workbench_boundaries, LOCAL_VALID_WITNESSES
    rows = local_workbench_boundaries()
    assert {row['command'] for row in rows} == set(LOCAL_VALID_WITNESSES)
    assert all(row['url'] is None and row['local_execution_witnesses'] and row['hosted_rejection_witness'] for row in rows)
    assert all(row['local_lifecycle_coverage'] == 'local_lifecycle_scenario' for row in rows)


def test_workbench_family_references_resolve_to_real_tests():
    import ast
    from pathlib import Path
    from tests.mcp_coverage import workbench_family_policies
    policies = workbench_family_policies()
    assert len(policies) == 14
    for family, (witness, limits) in policies.items():
        assert witness and limits, family
        filename, name = witness.split('::')
        module = ast.parse(Path(filename).read_text())
        assert any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
                   for node in module.body), (family, witness)


def test_material_variant_inventory_is_finite_and_does_not_hide_open_cases():
    from bookflow.core import registry
    from tests.mcp_coverage import schema_variants, workbench_variant_map, variant_policies
    registry.load_all()
    rows=[{'command':cmd.name,'url':'inventory-only','schema_variants':schema_variants(cmd.input_model.model_json_schema())}
          for cmd in registry.routed_commands()]
    mapped=workbench_variant_map(rows)
    assert len(mapped)==len(variant_policies())==22
    assert sum(len(group["paths"]) for group in mapped)==2326
    assert all(group['browser_witnesses'] for group in mapped)
    # The full GUI gate is still OPEN; don't silently relabel schema nodes as
    # accepted journeys. This test guards the accounting, not their acceptance.
    assert any(group['remaining_material_cases'] for group in mapped)
    import ast
    from pathlib import Path
    for group in mapped:
        for witness in group['browser_witnesses']:
            filename,name=witness.split('::')
            assert any(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==name
                       for node in ast.parse(Path(filename).read_text()).body),witness
