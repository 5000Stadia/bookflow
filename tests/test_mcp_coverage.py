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
