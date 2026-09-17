import ast
import json
import re
from pathlib import Path

from tests.mcp_coverage import execution_map


def resolves(witness):
    """The named test function exists in the named file, or the row is a claim about nothing.

    Read this for exactly what it says. It parses the file and looks for a function of that
    name; it does not import the module, collect the test, or run it. **A witness that exists
    and cannot run resolves fine**, and that is not hypothetical: five witness tests spent a
    day dying on a TypeError inside `Matrix.open`, before driving a single command, while the
    commands they witness sat in rows this function was happy with.

    Static resolution is the cheap half of the check and is worth keeping -- a cited name that
    was renamed or deleted is caught here in milliseconds. The other half cannot be done
    statically, because the breakage is at run time: it needs the witnesses actually executed.
    `scripts/witness-suite.sh` derives this ledger's distinct witnesses and runs them, and that
    is what a parity claim rests on. Do not read a green run of this file as a parity claim.
    """
    filename, name = witness.split('::')
    module = ast.parse(Path(filename).read_text())
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
               for node in module.body)


def test_registry_execution_ledger_has_no_unclassified_commands(tmp_path):
    rows = execution_map()
    assert len(rows) == 474
    assert sum(row['coverage'] == 'local_lifecycle_scenario' for row in rows) == 5
    # The claim worth asserting: every registered command either has an executed witness, or a
    # recorded, dated reason it does not. A row with NEITHER is a command that shipped unproven
    # and unexplained, which is exactly what `payment delete` was on 2026-09-15 -- registered,
    # tested, reviewed, and unreachable over every transport but in-process Python.
    #
    # The two cases are not the same and the ledger must not flatten them. An unwitnessed command
    # in a feature the human has deferred is honest; an unwitnessed command in a shipped one is a
    # lie. So a pending row is allowed, and it has to say why it is pending and since when.
    #
    # What used to sit here instead was a count of `four_surface_scenario` rows -- which the
    # ledger emitted for ANY witness, so a two-surface test raised the number exactly as a
    # four-surface one did. That count could only ever confirm itself.
    for row in rows:
        assert row['execution_witness'] or row['pending_reason'], (
            row['command'], 'ships with no transport witness and no recorded reason')
        if row['coverage'].startswith('pending'):
            assert row['pending_reason'], row['command']
            since, why = row['pending_reason']
            assert re.fullmatch(r'\d{4}-\d\d-\d\d', since), (row['command'], since)
            assert len(why) > 80, (row['command'], 'a reason has to say enough to be checkable')
    # A row may claim all four surfaces only if its witness declares them. Twelve do today; the rest
    # are `transport_scenario` -- real executed evidence over at least one real transport, which is
    # a weaker claim honestly made. Adding `SURFACES` to a witness that does drive all four is
    # mechanical and moves its rows up.
    for row in rows:
        if row['coverage'] == 'four_surface_scenario':
            assert row['surfaces'] and set(row['surfaces']) == {'python', 'cli', 'http', 'mcp'}, row
        if row['surfaces']:
            assert set(row['surfaces']) <= {'python', 'cli', 'http', 'mcp'}, row
    assert all(row['local_valid_witnesses'] for row in rows if row['coverage'] == 'local_lifecycle_scenario')
    assert {row['mode'] for row in rows} == {'routed_json', 'advisory', 'binary_input', 'binary_output', 'local_lifecycle', 'standalone_local', 'standalone_protocol', 'finite_poll_with_local_follow'}
    # A witness that does not exist is the failure this ledger exists to catch, so
    # every name it cites has to resolve to a real test before the count means anything.
    for row in rows:
        # A pending row cites nothing, by definition; its honesty is checked above.
        if row['execution_witness']:
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
    assert len(mapped)==len(variant_policies())==25
    # The census of material schema nodes. It moves whenever a routed command gains input
    # shape. Measured from the merged tree on every merge -- no branch's number survives
    # another branch landing.
    #
    # 2694 -> 2766 since 82e3a13, and every one of the 72 is accounted for by a command that
    # landed. Re-measure it the same way when it moves again -- per-command node counts on both
    # trees, differenced -- because a census bumped without that arithmetic hides new shape.
    #   +64  the nine job-time verbs, which have no transport witness on purpose (see
    #        JOB_TIME_COMMANDS): time-activity sales-receipt 15, invoice 14, query 12, update 12,
    #        create 6, show 2, and one node each for billing, history and void.
    #    +4  the three delete verbs that closed the deletion set: deposit delete 2, credit-memo
    #        delete 1, journal delete 1.
    #    +4  a customer refund may now be drawn on an overpayment as well as a credit memo:
    #        customer-refund post and update each gained /sources/[]/payment beside
    #        /sources/[]/credit_memo.
    #
    # The run before this one moved 2565 -> 2694 since 666465a: +79 from 18 commands that did not
    # exist, +34 from the Items grid on cheque and card-charge post/update, +10 from /receipts on
    # bill post/update, +4 from membership grant/revoke, +2 from customer-refund show's
    # revision_number and company attach's administrator.
    assert sum(len(group["paths"]) for group in mapped)==2766
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
