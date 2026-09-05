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


def _write(verb, model):
    def planner(inp, ctx, s):
        return journals.prepare(s, ctx, inp, verb)
    cmd = command('journal ' + verb, scope='company', description={
        'post': 'Post two through 200 balanced domestic journal lines with an explicit or automatically allocated number and typed header custom fields.',
        'update': 'Append an immutable correction with an exact old-date reversal and a full new-date replacement.',
        'void': 'Void a journal with a required context reason and an exact reversal at its current accounting date.',
    }[verb], input_model=model, output_model=JournalWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['journal'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('journal show', 'journal', 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_UNBALANCED_ENTRY',
                     'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE',
                     'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED'])(planner)
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


JOURNAL_COMMANDS = [journal_post, journal_show, journal_update, journal_void, journal_query, journal_history]
