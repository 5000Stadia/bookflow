"""Journal lifecycle commands; noun discovery is owned by registry integration."""
from bookflow.core.registry import command, Plan
from bookflow.company import journals
from bookflow.company.journal_models import (
    JournalPostInput, JournalUpdateInput, JournalVoidInput, JournalShowInput,
    JournalQueryInput, JournalHistoryInput,
)
from bookflow.company.journal_outputs import (
    JournalOutput, JournalWriteOutput, JournalPageOutput, JournalHistoryOutput,
)
from bookflow.commands.common import UNSURE_ACCOUNT


def _write(verb, model):
    def planner(inp, ctx, s):
        return journals.prepare(s, ctx, inp, verb)
    cmd = command('journal ' + verb, scope='company', description={
        'post': 'Post two through 200 balanced journal lines, converting foreign amounts at exact-date stored or explicit manual rates and capturing original money and typed custom fields.' + UNSURE_ACCOUNT,
        'update': 'Append an immutable correction with an exact old-date reversal and a full new-date replacement.',
        'void': 'Void a journal with a required context reason and an exact reversal at its current accounting date.',
    }[verb], input_model=model, output_model=JournalWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['journal'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('journal show', 'journal', 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_UNBALANCED_ENTRY',
                     'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE',
                     'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED'] + (['E_NO_EXCHANGE_RATE'] if verb != 'void' else []))(planner)
    cmd.ledger = True
    cmd.applier(journals.apply)
    return cmd


journal_post = _write('post', JournalPostInput)
journal_update = _write('update', JournalUpdateInput)
journal_void = _write('void', JournalVoidInput)


@command('journal show', scope='company', description='Show a journal and its current or selected immutable revision, historical lines, captured custom fields and posting batch totals.',
         input_model=JournalShowInput, output_model=JournalOutput, required_role='member', capability='ledger.read',
         positional=['journal'], error_codes=['E_RECORD_NOT_FOUND'])
def journal_show(inp, ctx, s):
    return Plan(journals.show(s, inp))


@command('journal query', scope='company', description='Page journals in accounting-date and stable-id order; restart on company audit changes.',
         input_model=JournalQueryInput, output_model=JournalPageOutput, required_role='member', capability='ledger.read',
         error_codes=['E_QUERY_STALE'])
def journal_query(inp, ctx, s):
    return Plan(journals.page(s, ctx, inp))


@command('journal history', scope='company', description='Page immutable journal revisions in revision-number order, including all associated correction and void batches.',
         input_model=JournalHistoryInput, output_model=JournalHistoryOutput, required_role='member', capability='ledger.read',
         positional=['journal'], error_codes=['E_QUERY_STALE', 'E_RECORD_NOT_FOUND'])
def journal_history(inp, ctx, s):
    return Plan(journals.page(s, ctx, inp, history=True))


def _delete():
    from bookflow.company import journal_deletions as deletion
    from bookflow.company.journal_deletion_models import JournalDeleteInput, JournalDeleteOutput
    from bookflow.core.deletion_families import capability as delete_capability

    def planner(inp, ctx, s):
        return deletion.prepare(s, ctx, inp)

    def recover(inp, ctx, s):
        return deletion.recover(inp, ctx, s)

    cmd = command('journal delete', scope='company',
        description='Delete this journal entry with a required reason and exact expected_version. Cancel its accounting at its original date; retain immutable history and its number. Requires the explicit family Delete grant and ledger.read, independently of ledger.post. A check, card charge, transfer, inventory document or item receipt is refused by name and deleted or voided through the command that owns it. Reconciled, settled or closed effects refuse atomically.',
        input_model=JournalDeleteInput, output_model=JournalDeleteOutput, writes={'company'},
        required_role='standard', capability=delete_capability(deletion.FAMILY), explicit_grant_only=True,
        accepts_idempotency_key=True, positional=['journal'],
        version_source=('journal show', 'journal', 'version'),
        error_codes=['E_RECORD_NOT_FOUND','E_VERSION_CONFLICT','E_VALIDATION','E_REASON_REQUIRED',
                     'E_PERIOD_CLOSED','E_RECONCILIATION_DEPENDENCY','E_DEPOSIT_DEPENDENCY',
                     'E_HAS_APPLICATIONS','E_IDEMPOTENCY_MISMATCH'])(planner)
    cmd.resource_requirements = (('ledger.read', 'member'),)
    cmd.ledger = True
    cmd.permanent_recovery = recover
    cmd.applier(deletion.apply)
    return cmd


journal_delete = _delete()

JOURNAL_COMMANDS = [journal_post, journal_show, journal_update, journal_void, journal_query,
                    journal_history, journal_delete]


def restore_journal(inp, ctx, s):
    from bookflow.company import restorations
    plan = restorations.prepare_journal(s, ctx, inp)
    plan.data['input'] = inp
    return plan


def _restore():
    from bookflow.company import restorations
    from bookflow.company.restoration_models import JournalRestoreInput, RestoreOutput
    from bookflow.core.deletion_families import capability as delete_capability
    cmd = command('journal restore', scope='company',
        description='Restore a deleted journal entry as it stood before deletion: post a new entry with the same lines, accounts, names, classes, memo and custom fields through journal post, and link it to the deleted one in the audit trail. The deleted entry stays deleted with its number and history; the new one takes the next number unless number is given. Posts at the deleted entry\'s date unless date is given; a closed period, an inactive account or name refuses. People only: an agent is refused. Requires the explicit journal Delete grant and ledger.post. Asking again for the same deleted entry answers with the first restoration and posts nothing.',
        input_model=JournalRestoreInput, output_model=RestoreOutput, writes={'company'},
        required_role='standard', capability=delete_capability('journal_entry'), explicit_grant_only=True,
        accepts_idempotency_key=True, positional=['journal'],
        error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERMISSION', 'E_PERIOD_CLOSED', 'E_UNBALANCED_ENTRY',
                     'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE', 'E_NO_EXCHANGE_RATE'])(restore_journal)
    cmd.resource_requirements = (('ledger.post', 'standard'),)
    cmd.ledger = True
    cmd.applier(restorations.apply_journal)
    return cmd


journal_restore = _restore()
JOURNAL_COMMANDS.append(journal_restore)
