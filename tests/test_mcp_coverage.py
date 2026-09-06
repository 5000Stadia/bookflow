import json
from tests.mcp_coverage import execution_map


def test_registry_execution_ledger_has_no_unclassified_commands(tmp_path):
    rows = execution_map()
    assert len(rows) == 276
    assert sum(row['coverage'] == 'four_surface_scenario' for row in rows) == 271
    assert sum(row['coverage'] == 'local_lifecycle_scenario' for row in rows) == 5
    assert all(row['execution_witness'] and not row['coverage'].startswith('pending') for row in rows)
    assert all(row['local_valid_witnesses'] for row in rows if row['coverage'] == 'local_lifecycle_scenario')
    assert {row['mode'] for row in rows} == {'routed_json', 'advisory', 'binary_input', 'binary_output', 'local_lifecycle', 'standalone_local', 'standalone_protocol', 'finite_poll_with_local_follow'}
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
