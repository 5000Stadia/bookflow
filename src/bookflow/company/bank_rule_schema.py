"""Bank rules and learning (co0119, R108 increment 4; design/bank-feeds.md §5, §9).

- ``bank_rules``: one rule for the bank inbox: which lines it fits (an account or every feed account, a direction,
  conditions that must all hold) and what it does (categorise: a payee, an account, a class, a memo; transfer: to an
  account; ignore: with a reason), its ``priority`` and ``source`` (written, or learned from two consistent answers),
  its ``status`` (active, suggested, disabled, retired) and, for a rule that acts on its own (an auto-add or ignore
  rule), the automation columns of design/principal-directives.md §3 (``state`` draft, live or paused, who
  established it). Never deleted; every change is a new row in ``bank_rule_versions``.
- ``bank_rule_versions``: append-only: each version of a rule as it was saved, who saved it and why.
- ``bank_rule_declines``: the payee keys a person or agent declined to have learned, per account; never suggested
  again. Append-only.

Three columns on ``bank_lines``: ``rule_id`` (the rule that proposed for the line, or acted on it), ``answer`` (what a
line was added as: payee, account and class) and ``payee_key`` (the party it names, indexed, which history proposals
and learning read). Two on ``company_info``: ``bank_rule_learning`` (ask, always or never) and
``bank_rule_automation`` (live auto-add and ignore rules act, or are held back company-wide).
"""
import sqlalchemy as sa

DIRECTIONS = ('in', 'out', 'any')
ACTIONS = ('categorise', 'transfer', 'ignore')
SOURCES = ('written', 'learned')
STATUSES = ('active', 'suggested', 'disabled', 'retired')
AUTOMATION_STATES = ('draft', 'live', 'paused')
FIELDS = ('payee', 'payee_key', 'memo', 'number', 'amount')
OPS = ('contains', 'starts_with', 'equals', 'between')
PAYEE_TYPES = ('customer', 'vendor', 'employee', 'other_name')
IGNORE_REASONS = ('already_entered', 'not_ours', 'duplicate', 'other')
LEARNING = ('ask', 'always', 'never')


def define_tables(metadata, C, T):
    from bookflow.company import automation

    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    rules = T('bank_rules',
        ident('id', 'Stable ULID of this rule.', primary_key=True),
        C('name', sa.String(200), 'Its name, as shown with every line it proposes for.', nullable=False),
        C('name_key', sa.String(200), 'The name case-folded, unique.', nullable=False),
        ident('account_id', 'The feed account it applies to; null for every feed account.', 'accounts.id',
              nullable=True),
        C('direction', sa.String(3), f'Lines it fits: {", ".join(DIRECTIONS)} (money in, money out, either).',
          nullable=False),
        C('conditions', sa.Text, 'JSON array of conditions, all of which must hold: field (payee, payee_key, memo, '
                                 'number, amount), op (contains, starts_with, equals, between), value and, for '
                                 'between, value_to. Text is compared case-folded with spaces collapsed; no regular '
                                 'expressions.', nullable=False),
        C('action', sa.String(10), f'What it does: {", ".join(ACTIONS)}.', nullable=False),
        C('payee_type', sa.String(16), 'Categorise: the list the payee is on.', nullable=True),
        C('payee_id', sa.String(26), 'Categorise: the payee (a customer, vendor, employee or other name).',
          nullable=True),
        C('payee', sa.String(1000), 'Categorise: the payee as shown; null when the rule names none.', nullable=True),
        ident('category_account_id', 'Categorise: the account an added line goes to.', 'accounts.id', nullable=True),
        ident('class_id', 'Categorise: the class an added line takes.', 'classes.id', nullable=True),
        C('memo', sa.Text, "Categorise: the memo an added line takes; null keeps the bank's text.", nullable=True),
        ident('transfer_account_id', 'Transfer: the other account of the transfer.', 'accounts.id', nullable=True),
        C('ignore_reason', sa.String(16), f'Ignore: the reason ({", ".join(IGNORE_REASONS)}).', nullable=True),
        C('auto_add', sa.Integer, '1 when the rule asks to add its lines unattended (a categorise rule); it acts only '
                                  'while live.', nullable=False, server_default='0'),
        C('priority', sa.Integer, 'Lower is tried first; written rules default to 100, learned rules to 1000.',
          nullable=False),
        C('source', sa.String(8), f'How it came: {", ".join(SOURCES)}.', nullable=False),
        C('status', sa.String(10), f'{", ".join(STATUSES)}: only an active rule fits lines; a suggested one waits '
                                   'to be accepted or declined.', nullable=False),
        ident('learned_from_batch', 'The import batch whose line taught a learned rule; reversing that import retires '
                                    'it unless it was edited since.', 'batches.id', nullable=True),
        C('learned_key', sa.String(200), 'A learned rule: the payee key it was learned for.', nullable=True),
        C('edited', sa.Integer, '1 once a person or agent changed it after it was made (a learned rule edited is '
                                'kept when its import is reversed).', nullable=False, server_default='0'),
        C('version', sa.Integer, 'Optimistic-concurrency version of this rule.', nullable=False),
        C('created_at', sa.String(32), 'UTC time it was made.', nullable=False),
        C('created_by', sa.String(26), 'Principal that made it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was made through.', nullable=False),
        C('updated_at', sa.String(32), 'UTC time of the last change.', nullable=False),
        C('updated_by', sa.String(26), 'Principal of the last change.', nullable=False),
        C('updated_via', sa.String(16), 'Interface of the last change.', nullable=False),
        *automation.columns(),
        sa.CheckConstraint(f'direction IN ({quoted(DIRECTIONS)})', name='ck_bank_rule_direction'),
        sa.CheckConstraint(f'action IN ({quoted(ACTIONS)})', name='ck_bank_rule_action'),
        sa.CheckConstraint(f'source IN ({quoted(SOURCES)})', name='ck_bank_rule_source'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_bank_rule_status'),
        sa.CheckConstraint(f'state IN ({quoted(AUTOMATION_STATES)})', name='ck_bank_rule_state'),
        sa.CheckConstraint(f'payee_type IS NULL OR payee_type IN ({quoted(PAYEE_TYPES)})',
                           name='ck_bank_rule_payee_type'),
        sa.CheckConstraint(f'ignore_reason IS NULL OR ignore_reason IN ({quoted(IGNORE_REASONS)})',
                           name='ck_bank_rule_ignore_reason'),
        sa.CheckConstraint("json_valid(conditions) AND json_type(conditions) = 'array'",
                           name='ck_bank_rule_conditions'),
        sa.CheckConstraint('auto_add IN (0, 1) AND edited IN (0, 1) AND version >= 1', name='ck_bank_rule_flags'),
        sa.CheckConstraint("auto_add = 0 OR action = 'categorise'", name='ck_bank_rule_auto_add'),
        sa.Index('ux_bank_rules_name', 'name_key', unique=True),
        sa.Index('ix_bank_rules_account', 'account_id', 'status'),
        sa.Index('ix_bank_rules_learned', 'learned_from_batch'),
        description='Bank inbox rules, written or learned: which lines each fits and what it proposes; an auto-add or '
                    'ignore rule acts on its own only while live. Never deleted.')
    versions = T('bank_rule_versions',
        ident('id', 'Stable ULID of this version.', primary_key=True),
        ident('rule_id', 'The rule.', 'bank_rules.id'),
        C('version', sa.Integer, 'The rule version this row records.', nullable=False),
        C('definition', sa.Text, 'The rule as saved at this version, as a JSON object.', nullable=False),
        C('change', sa.String(16), 'What was done: created, updated, accepted, declined, deactivated, live, paused, '
                                   'retired.', nullable=False),
        C('changed_by', sa.String(26), 'Principal that saved it.', nullable=False),
        C('changed_via', sa.String(16), 'Interface it came through.', nullable=False),
        C('changed_at', sa.String(32), 'UTC time it was saved.', nullable=False),
        C('reason', sa.String(500), 'Why, as given.', nullable=True),
        sa.CheckConstraint("json_valid(definition) AND json_type(definition) = 'object'",
                           name='ck_bank_rule_version_json'),
        sa.Index('ux_bank_rule_versions', 'rule_id', 'version', unique=True),
        description='Every saved version of each bank rule, append-only: who changed what, when and why.')
    declines = T('bank_rule_declines',
        ident('id', 'Stable ULID of this decline.', primary_key=True),
        ident('account_id', 'The feed account the suggestion was for.', 'accounts.id'),
        C('payee_key', sa.String(200), 'The payee key not to learn again.', nullable=False),
        ident('rule_id', 'The suggested rule that was declined.', 'bank_rules.id'),
        C('declined_by', sa.String(26), 'Principal that declined it.', nullable=False),
        C('declined_at', sa.String(32), 'UTC time it was declined.', nullable=False),
        sa.Index('ux_bank_rule_declines', 'account_id', 'payee_key', unique=True),
        description='Payee keys declined as learned rules, per account: never suggested again. Append-only.')
    return {'bank_rules': rules, 'bank_rule_versions': versions, 'bank_rule_declines': declines}


def line_columns(C):
    """The two columns co0119 adds to ``bank_lines``."""
    return (C('rule_id', sa.String(26), 'The rule that proposed for this line, or acted on it; null when none.',
              sa.ForeignKey('bank_rules.id'), nullable=True),
            C('answer', sa.Text, 'What the line was added as (payee, account, class) as a JSON object, read by '
                                 'history proposals and learning; null unless added with one account.',
              sa.CheckConstraint("answer IS NULL OR (json_valid(answer) AND json_type(answer) = 'object')",
                                 name='ck_bank_line_answer'), nullable=True),
            C('payee_key', sa.String(200), 'The payee as Bookflow reads it, when the text names a party (letters, & and a '
                                           "name's own short number; store and reference numbers, dates and processor "
                                           'words dropped); null for "CHECK 1234", "MOBILE DEPOSIT" and the like. '
                                           'History proposals and learning read lines by it.', nullable=True))


def company_info_columns(C):
    return (C('bank_rule_learning', sa.String(8), 'Bank rules: when two adds of a payee agree, ask (the default: a '
             'suggested rule to accept or decline), always (make it active) or never (learn nothing).',
             sa.CheckConstraint(f"bank_rule_learning IN ({', '.join(repr(v) for v in LEARNING)})",
                                name='ck_company_info_bank_rule_learning'), nullable=False, server_default='ask'),
            C('bank_rule_automation', sa.Boolean, 'Bank rules: live auto-add and ignore rules act at imports (true, the '
              'default); false holds every one back company-wide, their lines waiting with their proposals.',
              nullable=False, server_default='1'))


_FIXED = ('id', 'created_at', 'created_by', 'created_via', 'source', 'learned_from_batch', 'learned_key')
GUARDS = (
    "CREATE TRIGGER bank_rules_no_delete BEFORE DELETE ON bank_rules "
    "BEGIN SELECT RAISE(ABORT, 'bank rules are never deleted'); END",
    "CREATE TRIGGER bank_rules_fixed_update BEFORE UPDATE ON bank_rules WHEN "
    + ' OR '.join(f'NEW.{name} IS NOT OLD.{name}' for name in _FIXED)
    + " BEGIN SELECT RAISE(ABORT, 'a bank rule keeps how it came'); END",
    "CREATE TRIGGER bank_rule_versions_no_update BEFORE UPDATE ON bank_rule_versions "
    "BEGIN SELECT RAISE(ABORT, 'bank rule versions are append-only'); END",
    "CREATE TRIGGER bank_rule_versions_no_delete BEFORE DELETE ON bank_rule_versions "
    "BEGIN SELECT RAISE(ABORT, 'bank rule versions are append-only'); END",
    "CREATE TRIGGER bank_rule_declines_no_update BEFORE UPDATE ON bank_rule_declines "
    "BEGIN SELECT RAISE(ABORT, 'bank rule declines are append-only'); END",
    "CREATE TRIGGER bank_rule_declines_no_delete BEFORE DELETE ON bank_rule_declines "
    "BEGIN SELECT RAISE(ABORT, 'bank rule declines are append-only'); END",
)
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)
