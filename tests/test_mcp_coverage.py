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
    # 491 -> 492 on main before batch A (not measured here); +6 the card-credit verbs (R135).
    # +2 company backup and company restore (R133).
    # +4 agent/user deactivate and activate (R89); +1 report export (R145).
    # +1 reconcile import (R167).
    # +4 backup schedule, list, verify and rehearse (R156).
    # +3 report entries-to-review, report prior-balances and review mark (R163).
    # +3 cutover plan, apply and tie-out (R166).
    # +1 account uncategorized (Ask My Accountant); its read input adds no census node.
    # +4 sales-tax adjust and sales-tax adjustment show, query and void (R175).
    # +2 journal restore and invoice restore (R151).
    # +4 customer and vendor merge and unmerge (R117).
    # +1 payment bounce (R176).
    assert len(rows) == 528
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
    assert len(policies) == 16  # + delete_confirmation and dedicated_page (record and own-page commands)
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
    assert len(mapped)==len(variant_policies())==26
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
    # +1: customer-refund history adds its refund selector; limit/cursor are paging controls.
    #
    # 2797 -> 2858 at the batch A merge: main already measured 2817 (+20, not re-measured
    # per command here), and batch A adds +41: card-credit post 11, update 15, query 8, and
    # one each for show, history and void (+37, R135); invoice, sales-receipt, credit-memo and
    # statement-charge post each gain the null branch of an optional `date` (+4, R95).
    # 2858 -> 2860: company restore's optional organization and name each add their null branch (R133).
    # 2860 -> 2864, measured per command on the merged tree: agent deactivate/activate and user
    # deactivate/activate add one node each (+4, R89); report export adds none (R145).
    # 2864 -> 2876: R147 (sales line kinds) adds 12 input variants to the sales and quote posts.
    # 2876 -> 2878: user list and membership list gain optional paging (the cursor null branch each, R88).
    # 2878 -> 2881: sales-tax liability reads a period (R72 trial fix). Its optional as_of,
    # date_from and date_to each add one null branch: 2 -> 5 nodes on that command, measured per
    # command on both trees; the reconcile inputs are unchanged (only preview's output grew).
    # 2881 unchanged (V1.5 trials): payment receive's applications gains a third branch,
    # "suggested". The applications nodes of payment receive and of the prospective preview
    # requests move from the two-way Inline/Selection group to a new three-way group (variant
    # groups 25 -> 26) without adding a node; strategy is a literal, not a variant.
    # 2881 -> 2882 (V1.5 loose ends): payment query's optional `reference` (the customer's check
    # number) adds its null branch; no other routed input changed.
    # 2882 -> 2890 (R158, reconcile mark --all), measured per command on both trees: reconcile mark
    # 1 -> 9 nodes, +8. The new `filters` (the same CandidateFilter `reconcile candidates` takes)
    # carries eight optional fields, each adding its null branch: from_date, to_date, side,
    # producer, number, payee, memo, amount. `all` and `all_action` are a boolean and a literal and
    # add none; no other routed input changed. No new variant kind: filters has a default rather
    # than being nullable, so tests/mcp_coverage.py needs no new policy and the groups stay 26.
    # 2890 -> 2893 (merge of R167 onto R158): reconcile import adds its 3 nodes, as measured below.
    # 2882 -> 2885 (R167): reconcile import, a new command, measured per command: 3 nodes, the
    # null branches of its optional draft, statement_date and ending_balance. No other input moved.
    # 2893 -> 2896 (R156, scheduled backups), measured per command on both trees: backup schedule's
    # optional daily_at, destination and keep each add their null branch (+3); `off` is a boolean
    # and `company` a required string, and backup list, verify and rehearse take no input (0 each).
    # 2896 -> 2895 (R163), measured: reconcile preview and finish each lose the dormant
    # `adjustment` input and its nullable class_id, -4 (its Adjustment|null variant group goes,
    # 26 -> 25 groups); report entries-to-review /cursor, report prior-balances /cursor and
    # review mark /note add one null branch each, +3.
    # 2895 -> 2912 (R166, the move-in), measured per command on the merged tree: cutover plan and
    # cutover apply 6 each (the null branches of a file's attachment, content, name and kind, and of
    # clearing_account and journal_number), cutover tie-out 5 (no journal_number). No new variant
    # kind; the groups stay 25.
    # 2912 -> 2913 (merge of reconciliation adjustments onto R166), measured on the merged tree:
    # reconcile finish gains the person-only `adjustment` (DiscrepancyAdjustment|null, a reason and
    # nothing else; nobody chooses the account it posts to), its null branch the one new node. A new
    # variant kind, ('anyOf', ('DiscrepancyAdjustment', 'null')), so the groups go 25 -> 26. Preview
    # keeps no adjustment (R163).
    # 2913 -> 2914 (R168, demo reset refuses a root holding real books): demo reset's new optional
    # `force` (the data root's path, a nullable string) adds its null branch, +1. The closing-date
    # rule changes only a field description; no other routed input changed. Groups stay 26.
    # 2914 -> 2927 (R175, Adjust Sales Tax Due), measured per command: sales-tax adjust 5 (its
    # required amount's string|MoneyInput group and the MoneyInput's own optional amount, and the
    # null branches of memo, number and class_id), sales-tax adjustment query 7 (cursor, date_from,
    # date_to, agency, adjustment_account, number, status), void 1 (expected_version), show 0.
    # No new variant kind; the groups stay 26.
    # 2927 -> 2931 (R151), measured on the merged tree: journal restore and invoice restore each add
    # the null branches of their optional date and number. No new variant kind; the groups stay 26.
    # R117 (customer and vendor merge and unmerge) adds none: merged, into, reason and the idempotency key are
    # all required or context, so no optional branch is new. Re-measured on the merged tree: still 2931.
    # 2931 -> 2933: reconcile import 3 -> 5. It reads a statement kept as an attachment, so `content` is now
    # optional (+1, its null branch) and `attachment` is a new optional string (+1, its null branch).
    # 2933 -> 2943 (R176), measured per command: payment bounce, a new command, 10 nodes -- the null branches of
    # its optional bank_fee_amount, bank_fee_account, bank_fee_memo, customer_fee_amount, customer_fee_item,
    # customer_fee_account, customer_fee_number, customer_fee_memo, bank_account and expected_facts_fingerprint.
    # The fees are flat optional fields, not nested objects, so no new variant kind arises; the groups stay 26.
    # R177 adds none: payment receive's input is unchanged (a receipt of 0.00 is a value, and only descriptions moved).
    assert sum(len(group["paths"]) for group in mapped)==2943
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
