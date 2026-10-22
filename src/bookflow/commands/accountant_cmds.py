"""Accountant review (R121): the external accountant, the accountant's copy and the review checklist.

The work lives in ``company/accountant_review.py``. Designating an accountant, cancelling a copy and
importing an accountant's changes are a person's acts; an agent may prepare the hand-off (export a
copy, preview a change file, read the checklist) but never accepts an import.
"""
from bookflow.commands.common import Empty
from bookflow.company import accountant_review as acct
from bookflow.company import accountant_review_models as m
from bookflow.company.journal_outputs import JournalWriteOutput
from bookflow.core.registry import Plan, command

_JOURNAL_ERRORS = ['E_RECORD_NOT_FOUND', 'E_UNBALANCED_ENTRY', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER',
                   'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_VALIDATION', 'E_PERMISSION']


def _person(s, command):
    if s.actor is None or s.actor.kind != 'human':
        from bookflow.hub import people_only
        raise people_only.refusal(people_only.ROLES_RULE, command=command, capability='company')


# ------------------------------------------------------------------ the external accountant

accountant_add = command(
    'accountant add', scope='company',
    description=("Make a member of this company its external accountant, the anchor's External Accountant user: they read "
                 'everything, and write only adjusting journal entries (`journal post`, correcting or voiding only their own), '
                 'reclassifications (`accountant reclassify`), review marks (`accountant mark-entry`, `accountant '
                 'reviewed-through`) and notes, all dated on or before `through`, the end of the review period. Everything else '
                 'they try is refused, and every change is attributed to them. The user must be a person with standard or higher '
                 "access to the company. Run again to move the period end. A person's step: an agent is refused."),
    input_model=m.AccountantAddInput, output_model=m.AccountantWriteOutput, writes={'company'}, required_role='admin',
    capability='company', accepts_idempotency_key=True, positional=['user'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERMISSION'])


@accountant_add
def plan_accountant_add(inp, ctx, s) -> Plan:
    _person(s, 'accountant add')
    return acct.plan_add(s, ctx, inp)


accountant_add.applier(lambda plan, ctx, s: acct.apply_add(plan, ctx, s))

accountant_remove = command(
    'accountant remove', scope='company',
    description="End a member's external accountant designation; their membership is unchanged. A person's step.",
    input_model=m.AccountantUserInput, output_model=m.AccountantWriteOutput, writes={'company'}, required_role='admin',
    capability='company', accepts_idempotency_key=True, positional=['user'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_PERMISSION'])


@accountant_remove
def plan_accountant_remove(inp, ctx, s) -> Plan:
    _person(s, 'accountant remove')
    return acct.plan_remove(s, ctx, inp)


accountant_remove.applier(lambda plan, ctx, s: acct.apply_remove(plan, ctx, s))


@command('accountant list', scope='company', description="The company's external accountants and the end of each review period.",
         input_model=m.AccountantListInput, output_model=m.AccountantListOutput, required_role='member', capability='company')
def plan_accountant_list(inp, ctx, s) -> Plan:
    return Plan(acct.accountant_list(s, inp))


accountant_reclassify = command(
    'accountant reclassify', scope='company',
    description=("Move an amount an entry posted from one account to another, as the anchor's Client Data Review reclassifies: "
                 'an adjusting journal entry dated on the entry\'s date (or `date`) takes the amount (all of it by default) out of '
                 'from_account and into to_account; the entry itself is not rewritten, so its history stands. The external '
                 "accountant's, in these books or in an accountant's copy."),
    input_model=m.ReclassifyInput, output_model=JournalWriteOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['transaction'], error_codes=_JOURNAL_ERRORS)


@accountant_reclassify
def plan_accountant_reclassify(inp, ctx, s) -> Plan:
    return acct.plan_reclassify(s, ctx, inp)


accountant_reclassify.ledger = True
accountant_reclassify.applier(lambda plan, ctx, s: acct.apply_reclassify(plan, ctx, s))

accountant_mark_entry = command(
    'accountant mark-entry', scope='company',
    description=('The external accountant marks an entry on the entries-to-review list reviewed, as `review mark` does for the '
                 'owner; the mark says it was the accountant\'s.'),
    input_model=m.MarkEntryInput, output_model=m.MarkEntryOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['transaction'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERMISSION'])


@accountant_mark_entry
def plan_accountant_mark_entry(inp, ctx, s) -> Plan:
    return acct.plan_mark_entry(s, ctx, inp)


accountant_mark_entry.applier(lambda plan, ctx, s: acct.apply_mark_entry(plan, ctx, s))

accountant_reviewed_through = command(
    'accountant reviewed-through', scope='company',
    description=('The external accountant marks the books reviewed through a date (on or before their review period end, or '
                 "the copy's dividing date). The newest mark is the one `report accountant-review` shows."),
    input_model=m.ReviewedThroughInput, output_model=m.ReviewedThroughWriteOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['through'],
    error_codes=['E_VALIDATION', 'E_PERMISSION'])


@accountant_reviewed_through
def plan_accountant_reviewed_through(inp, ctx, s) -> Plan:
    return acct.plan_reviewed_through(s, ctx, inp)


accountant_reviewed_through.applier(lambda plan, ctx, s: acct.apply_reviewed_through(plan, ctx, s))


# ------------------------------------------------------------------ the accountant's copy

accountant_copy_export = command(
    'accountant-copy export', scope='company',
    description=("Make an accountant's copy, as the anchor does: a verified backup archive of the company (open it on the "
                 "accountant's machine with `company restore --as-copy`) whose books know the dividing date `through`, with a "
                 'small manifest beside it, in the company folder\'s exports/. Bookflow sends it nowhere; hand it over yourself. '
                 'While the copy is out these books refuse every change dated on or before the dividing date, and account edits '
                 "and list merges, until `accountant-copy import` takes the accountant's changes back or a person runs "
                 '`accountant-copy cancel`. The copy takes only adjusting entries, reclassifications, review marks and notes '
                 'dated on or before the dividing date. The dividing date must be after the closing date. An agent may prepare '
                 'this hand-off.'),
    input_model=m.CopyExportInput, output_model=m.CopyExportOutput, writes={'company'}, required_role='admin',
    capability='company', positional=['through'], error_codes=['E_VALIDATION', 'E_PERMISSION', 'E_IO', 'E_BACKUP_INVALID'])


@accountant_copy_export
def plan_copy_export(inp, ctx, s) -> Plan:
    return acct.plan_export(s, ctx, inp)


accountant_copy_export.applier(lambda plan, ctx, s: acct.apply_export(plan, ctx, s))


@command('accountant-copy status', scope='company',
         description=("Whether an accountant's copy is out and its dividing date (these books refuse changes on or before it), "
                      "or, in an accountant's copy, the date it is restricted to; with earlier copies."),
         input_model=Empty, output_model=m.CopyStatusOutput, required_role='member', capability='company')
def plan_copy_status(inp, ctx, s) -> Plan:
    return Plan(acct.status(s))


accountant_copy_cancel = command(
    'accountant-copy cancel', scope='company',
    description=("Remove the accountant's copy restrictions without importing, as the anchor's Remove Restrictions does: these "
                 "books take changes on or before the dividing date again, and the copy's change file can no longer be imported. "
                 "Needs a reason. A person's step."),
    input_model=Empty, output_model=m.CopyCancelOutput, writes={'company'}, required_role='admin', capability='company',
    error_codes=['E_RECORD_NOT_FOUND', 'E_REASON_REQUIRED', 'E_PERMISSION'])


@accountant_copy_cancel
def plan_copy_cancel(inp, ctx, s) -> Plan:
    _person(s, 'accountant-copy cancel')
    return acct.plan_cancel(s, ctx)


accountant_copy_cancel.applier(lambda plan, ctx, s: acct.apply_cancel(plan, ctx, s))

accountant_copy_changes = command(
    'accountant-copy changes', scope='company',
    description=("In an accountant's copy: write the change file to send back, in the company folder's exports/. It holds "
                 'every adjusting entry and reclassification made in the copy (each as it stands now; voided ones are left out), '
                 "notes, review marks and reviewed-through marks, signed so the owner's books can tell it came unaltered from "
                 'this copy. Run it again after more work; the owner imports each change once.'),
    input_model=Empty, output_model=m.CopyChangesOutput, writes={'company'}, required_role='admin', capability='company',
    error_codes=['E_PERMISSION', 'E_IO'])


@accountant_copy_changes
def plan_copy_changes(inp, ctx, s) -> Plan:
    return acct.plan_changes(s, ctx)


accountant_copy_changes.applier(lambda plan, ctx, s: acct.apply_changes(plan, ctx, s))


@command('accountant-copy preview', scope='company',
         description=("Verify an accountant's change file against these books and list each change: new (can be taken), "
                      'imported (already taken) or refused (and why). A file changed after the copy wrote it, or from another '
                      "company's or another copy's books, is refused whole. Nothing is changed."),
         input_model=m.FileInput, output_model=m.ChangesFileOutput, required_role='admin', capability='company',
         positional=['file'], error_codes=['E_VALIDATION'])
def plan_copy_preview(inp, ctx, s) -> Plan:
    return Plan(acct.preview(s, ctx, inp))


accountant_copy_import = command(
    'accountant-copy import', scope='company',
    description=("Take an accountant's changes back from their change file, as the anchor's Import Accountant's Changes: every "
                 'new change, or only the keys in `changes`. Each is posted as the accountant, under its own audit event naming '
                 "the copy and the accountant's reason; a change already taken is skipped, so importing twice changes nothing. "
                 "The copy comes back: these books take changes on or before the dividing date again. A person's step: an agent "
                 'may preview, never import.'),
    input_model=m.CopyImportInput, output_model=m.CopyImportOutput, writes={'company'}, required_role='admin',
    capability='company', positional=['file'], error_codes=_JOURNAL_ERRORS)


@accountant_copy_import
def plan_copy_import(inp, ctx, s) -> Plan:
    _person(s, 'accountant-copy import')
    return acct.plan_import(s, ctx, inp)


accountant_copy_import.ledger = True
accountant_copy_import.applier(lambda plan, ctx, s: acct.apply_import(plan, ctx, s))


# ------------------------------------------------------------------ review support

@command('report accountant-review', scope='company',
         description=("The period review checklist, after the anchor's Client Data Review: entries to review, what waits in the "
                      'Uncategorized accounts, bank and card accounts not reconciled through as_of, open receivables, payables and '
                      "undeposited funds, and the accountant's reviewed-through mark, each with whether it is done."),
         input_model=m.ReviewChecklistInput, output_model=m.ReviewChecklistOutput, required_role='member', capability='reports',
         error_codes=['E_VALIDATION'])
def plan_review_checklist(inp, ctx, s) -> Plan:
    return Plan(acct.checklist(s, inp))


@command('report accountant-changes', scope='company',
         description=("What an external accountant changed, newest first: in these books, and what came back from an accountant's "
                      'copy (from_copy), each with its reason and the records written, with counts of journal entries, '
                      'reclassifications, notes and review marks.'),
         input_model=m.AccountantChangesInput, output_model=m.AccountantChangesOutput, required_role='member', capability='reports')
def plan_accountant_changes(inp, ctx, s) -> Plan:
    return Plan(acct.changes_report(s, inp))
