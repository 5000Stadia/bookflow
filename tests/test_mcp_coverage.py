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
    # +1 vendor 1099-opening (R179).
    # +1 attachment text (R170 blind July: a statement's summary box read over MCP).
    # +1 transaction find (R138).
    # +1 item convert (R154).
    # +1 finance-charge assess (R120).
    # +1 vendor-credit refund (R149: a vendor's refund check for an open credit).
    # +18 budgets, budget and comparison reports, memorized reports (R109).
    # +8 prepayment receive, attach, apply, unapply, refund, show, query and report customer-prepayments (R124).
    # +4 account merge, account unmerge, item merge, item unmerge (R117, second part).
    # +8 the sales order (R101): sales-order create, update, copy, show, query, history, void; estimate sales-order.
    # +10 the five fixed-asset and five loan commands (R119).
    # +7 assembly build, update, finalize, void, delete, show and list (R103).
    # +1 report open-sales-orders (R102).
    # +18 mileage (R118): vehicle create/update/show/list, mileage-rate set/delete/list, trip create/update/void/show/
    # query/invoice/sales-receipt/billing/reimburse, report mileage-by-vehicle and mileage-by-job.
    # +2 customer-credit transfer, customer-credit transfer-void (R148).
    # +9 price rules (R110): price-rule create/update/activate/deactivate/show/list/preview, preferences, set-preferences.
    # +2 sales-order invoice and sales-order billing (R101 increment 4).
    # +2 sales-order purchase-order and work-order purchase-order (R101 increment 5).
    # +1 purchase-order close-lines (R153).
    # +15 timekeeping (R106): timesheet show/copy-last/save/preferences/set-preferences, clock in/out/cancel/status/
    # link, schedule create/update/cancel/show/calendar.
    # +13 inventory-site, inventory-transfer and report quantity-by-site (R104 increment 1).
    # +14 accountant review (R121): accountant add/remove/list/reclassify/mark-entry/reviewed-through, accountant-copy
    # export/status/cancel/changes/preview/import, report accountant-review and accountant-changes.
    # +9 check printing (R152): check print-queue, print, print-confirm, print-cancel, reprint, print-run, print-pdf,
    # print-setup and print-alignment.
    # +11 todo create, update, complete, show, list; notice generate, list, show, mark-sent, dismiss; reminders (R111).
    assert len(rows) == 686
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
    # 26 -> 27 (R150): unit prices and costs take SalesPriceInput, the shape of SalesMoneyInput with an
    # amount exact to three places past the minor unit. Measured per command on both trees: the same
    # 3430 paths, now split across one more variant kind.
    assert len(mapped)==len(variant_policies())==27
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
    # +19 (R178, vendor credit item rows), measured per command on both trees, as path sets
    # differenced: vendor-credit post 11 -> 21 (+10) and update 15 -> 24 (+9). Both gain the bill's
    # Items grid, nine nodes: the nullable /items collection and its rows' optional amount (the
    # string|money group and the MoneyInput's own amount), class_id, customer, description, line_id
    # and unit_cost (with its own money group). Post gains one more: its Expenses grid is now
    # optional too, so /expenses/[]/... moves to /expenses/anyOf/0/[]/... (six nodes renamed) under
    # a new nullable /expenses node. No new variant kind; the groups stay 26.
    # +3 (R179), measured per command: vendor 1099-opening 3 (its required amount's string|MoneyInput
    # group, the MoneyInput's own optional amount, and expected_version's null branch). cutover plan and apply stay
    # at 6 and tie-out at 5 (a file's kind gains three literal values, not a node); reconcile start stays at 4.
    # Re-measured on the merged tree (R176/R177 + R178 + R179): 2933 + 10 + 19 + 3 = 2965.
    # 2965 -> 2967 (R170 blind July fixes), measured per command on both trees: reconcile import 5 -> 7, the null
    # branches of its new optional limit and cursor (a long statement read in pages). attachment text, a new
    # command, adds none: its one input, the attachment id, is required, as attachment get's is (0 nodes).
    # No new variant kind; the groups stay 26.
    # 2967 -> 2969 (fix batch 5), measured per command: cutover plan 6 -> 8, the null branches of its new optional
    # limit and cursor (a long plan read in pages; it now takes CutoverPlanInput). cutover apply stays at 6, journal
    # post at 10 (a journal line's account gains a description, not a node). No new variant kind; the groups stay 26.
    # 2969 -> 2981 (R138 transaction find), measured: the new command adds 12, the null branches of its twelve
    # optional filters (types, name, name_type, number, amount, amount_from, amount_to, date_from, date_to, memo,
    # account, cursor). No new variant kind; the groups stay 26.
    # 2981 -> 2989 (R154), measured: item convert, a new command, 8 nodes -- the null branches of its optional
    # date, cogs_account, asset_account, income_account, description, purchase_description, price and cost.
    # No new variant kind; the groups stay 26.
    # 2989 -> 3002 (R120 finance charges): company update gains seven optional finance charge preferences (their
    # null branches, 7), and finance-charge assess's optional date, customers, amount, mark_to_print, memo and
    # expected_facts_fingerprint (6). No new variant kind.
    # 3002 -> 3013 (R149): vendor-credit refund is new with 7 (its optional expected_version, amount, number, memo,
    # check_number and payment_method); vendor-credit apply and unapply gain 4 between them (the refund-deposit
    # rows' optional amount and expected_version, and unapply's optional deposits). No new variant kind.
    # 3013 -> 3061 (R109), measured: eighteen new commands' optional inputs (budget, report and memorized-report
    # filters, dates, customer/class selectors, expected_version and the like) -- their null branches.
    # No new variant kind.
    # 3061 -> 3093 (R124), measured: the eight prepayment commands, 32 nodes -- the null branches of their optional
    # fields: receive 9, attach 1, apply 5, refund 7, show 3, query 5, report customer-prepayments 2 (unapply none).
    # apply_prepayment on the invoice conversions is a plain boolean and adds none. No new variant kind; the groups stay 26.
    # 3093 -> 3221 (R101, the sales order), measured per command on both trees: sales-order create 51 and update 54
    # (the work family's optional header and line fields, as estimate create and update have), query 12, estimate
    # sales-order 4, copy 3, show 2, history 1 and void 1 -- 128. No existing command moved: a line's new
    # cancelled_quantity and close are not nullable. No new variant kind; the groups stay 26.
    # 3221 -> 3254 (R119), measured: the ten fixed-asset and loan commands, 33 nodes -- the null branches of their
    # optional fields (setup's terms and accounts, depreciate's assets and memo, dispose's deposit_to and memo, the
    # loan's optional accounts, lender, origination, payment and update fields, pay's date, account, number, extra
    # principal and memo). No new variant kind.
    # 3254 -> 3265 (R103 assembly builds), measured: 11 null branches -- build's memo; update's expected_version,
    # quantity, date and memo; finalize's expected_version and date; void's and delete's expected_version; list's
    # status and item. No new variant kind.
    # 3265 -> 3269 (R102), measured: report open-sales-orders is new with 4, the null branches of its optional
    # customer, item, date_from and cursor (date_to is required). No existing command moved. No new variant kind.
    # 3269 -> 3343 (R118, mileage), measured per command: trip create 10, update 13, query 6, invoice 14,
    # sales-receipt 15 (the work billing conversion's optional fields, as time-activity's have), billing 1,
    # reimburse 4, vehicle create 1 and update 4, report mileage-by-vehicle 3 and mileage-by-job 3 -- 74. No
    # existing command moved. No new variant kind; the groups stay 26.
    # 3343 -> 3355 (R148), measured: payment receive's optional discount_class, write_off_account and
    # write_off_class wherever the receive input is carried, and customer-credit transfer's optional credit_memo,
    # payment, memo, expected_facts_fingerprint and apply_to amount -- their null branches. underpayment is a
    # defaulted literal and adds none. No new variant kind.
    # 3355 -> 3399 (R110, price rules), measured per command: price-rule create 17, update 19, preview 4, list 2,
    # activate 1, deactivate 1 (show, preferences and set-preferences 0) -- 44. No existing command moved: line
    # inputs are unchanged, only the captured line facts gained pricing_rule. No new variant kind.
    # 3399 -> 3411 (R101 increment 4), measured per command: sales-order invoice 11 (the null branches of its
    # optional number, line_ids, selections, memo, ar_account, terms, due_date and fingerprint, and an order
    # selection's line_id and item), sales-order billing 1 (cursor). release_to_order on invoice update, void and
    # delete is a defaulted choice and adds none. No new variant kind.
    # 3411 -> 3423 (R101 increment 5), measured per command: sales-order purchase-order and work-order purchase-order
    # 6 each (the null branches of their optional vendor, lines, expected_date and memo, and a line's line_id and
    # item). No existing command moved: purchase-order show gains an output field only. No new variant kind.
    # 3423 -> 3430 (R153), measured per command: bill post's optional order_lines adds 6 (its null
    # branch, quantity's, unit_cost's and amount's null and money-object branches), and purchase-order
    # close-lines adds 1 (expected_version's null branch). No new variant kind.
    # 3430 -> 3472 (R106, timekeeping), measured per command: clock in 6, out 7, cancel 1, status 1, link 1;
    # schedule create 3, update 7, cancel 1, calendar 3; timesheet show 2, copy-last 2, save 5, set-preferences 3
    # -- 42. No existing command moved. No new variant kind; the groups stay 26.
    # 3472 -> 3549 (R104 increment 1): the thirteen inventory-site, inventory-transfer and quantity-by-site
    # commands, plus the null branch of the optional `site` now on every stock-moving line and header (sales and
    # work lines, credit-memo lines, bill item lines shared by checks, card charges, vendor credits and receipts,
    # purchase-order lines and headers, inventory adjust, assembly build and update, item update's site_settings,
    # the stock reports' site filter, and report sales-by-item's site). No new variant kind.
    # 3549 -> 3557 (R121), measured per command: accountant reclassify 3 (amount, date, memo), accountant add,
    # mark-entry and reviewed-through 1 each (note), accountant-copy import 1 (changes), accountant-copy export 1 (accountant) -- their null branches.
    # No existing command moved. No new variant kind.
    # 3557 -> 3574 (R152, check printing), measured: +17 from the nine check print commands' optional fields
    # (account, first_number, style, checks, failed_from, failed, new_number, offsets, expected_version null branches)
    # and to_be_printed on check post, check update and bill pay. No new variant kind.
    # 3574 -> 3609 (R111): the eleven todo, notice and reminders commands' optional inputs, company update's five
    # scheduler and overdue notice preferences, and the optional expected_facts_fingerprint on memorized process, retry,
    # skip, enter and memorized-group enter, their null branches. No new variant kind.
    assert sum(len(group["paths"]) for group in mapped)==3609
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
