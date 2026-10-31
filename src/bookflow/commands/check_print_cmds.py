"""Print Checks (R152): the print queue, print runs, "Did checks print OK?", reprints and alignment.

The work lives in ``company/check_printing.py`` and the paper layout in ``documents/check_pdf.py``.
Queueing (``to_be_printed`` on ``check post`` and ``bill pay``), preparing a run and setting up the
printer are an agent's to do as a person's; confirming a run, cancelling one and reprinting a check
say what happened to paper in a printer, so they are a person's act.
"""
from bookflow.company import check_printing as p
from bookflow.company import check_print_models as m
from bookflow.core.registry import Plan, command


def _write(name, description, inp, out, planner, applier, *, errors, positional=()):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='ledger.post',
                  accepts_idempotency_key=True, positional=list(positional), error_codes=errors)(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   required_role='member', capability='ledger.read', positional=list(positional),
                   error_codes=errors)(planner)


def plan_print_queue(inp, ctx, s) -> Plan:
    return Plan(p.queue(s, inp))


def plan_print(inp, ctx, s) -> Plan:
    return p.prepare_print(s, ctx, inp)


def plan_print_confirm(inp, ctx, s) -> Plan:
    return p.prepare_confirm(s, ctx, inp)


def plan_print_cancel(inp, ctx, s) -> Plan:
    return p.prepare_confirm(s, ctx, inp, cancel=True)


def plan_reprint(inp, ctx, s) -> Plan:
    return p.prepare_reprint(s, ctx, inp)


def plan_print_run(inp, ctx, s) -> Plan:
    return Plan(p.show_run(s, inp))


def plan_print_pdf(inp, ctx, s) -> Plan:
    return Plan(p.pdf(s, inp))


def plan_print_setup(inp, ctx, s) -> Plan:
    return p.prepare_setup(s, ctx, inp)


def plan_print_alignment(inp, ctx, s) -> Plan:
    return Plan(p.alignment_sample(s, inp))


_TAIL = ['E_IDEMPOTENCY_MISMATCH', 'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE']

check_print_queue = _read(
    'check print-queue', "List the cheques waiting to be printed, by bank account, as the anchor's Select Checks to Print"
    ' does: checks saved with `to_be_printed` and bill payments by check paid `to_be_printed`, oldest first, with the'
    ' first check number Print would propose, the printer setup, and any print run still waiting for "Did checks print OK?".',
    m.CheckPrintQueueInput, m.CheckPrintQueueOutput, plan_print_queue, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])

check_print = _write(
    'check print', 'Print the queued cheques of one bank account. `first_number` is the number on the first check loaded'
    ' in the printer (default: the account\'s next check number); each cheque takes the next number in order, and a'
    ' number already used on the account is refused rather than skipped. Choose a `style` (voucher, standard, wallet)'
    ' and, for standard or wallet sheets, `checks_on_first_page` for a partial first sheet. The run and its numbers'
    ' are recorded and the numbers held until someone answers `check print-confirm`; get the PDF with'
    ' `check print-pdf`. A cheque dated in a closed period is refused. An agent may prepare a run; confirming it is'
    " a person's act. batch_id and next identify the whole-run reversal preview; cancellation is possible only"
    ' while every cheque is pending. A printed cheque cannot be recalled.',
    m.CheckPrintInput, m.CheckPrintWriteOutput, plan_print, p.apply_print,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_DUPLICATE_NUMBER', 'E_PERIOD_CLOSED', 'E_VALUE_RANGE', *_TAIL])

check_print_confirm = _write(
    'check print-confirm', 'Answer "Did checks print OK?" for a print run. With nothing else, every check printed and'
    ' carries its number from now on. `failed_from` names the first check that did not print correctly -- it and'
    ' every later one go back to the queue for a fresh number -- or `failed` lists the ones that did not; their numbers'
    ' are recorded as spoiled, so `report missing-checks` explains them and they are never handed out again. A'
    " person's act: it says what happened to paper in the printer.",
    m.CheckPrintConfirmInput, m.CheckPrintWriteOutput, plan_print_confirm, p.apply_confirm,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERMISSION', *_TAIL], positional=['run'])

check_print_cancel = _write(
    'check print-cancel', 'Cancel a print run that never reached paper: its cheques go back to the queue and its numbers'
    " are released unspoiled. A person's act.",
    m.CheckPrintRunInput, m.CheckPrintWriteOutput, plan_print_cancel, p.apply_confirm,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERMISSION', *_TAIL], positional=['run'])

check_reprint = _write(
    'check reprint', 'Print one numbered check or bill payment cheque again. Without `new_number` it prints with the'
    ' number it has and nothing about it changes. With `new_number` it prints on a new check: the old number is'
    ' recorded as spoiled and the cheque carries the new one (refused for a cheque dated in a closed period). Get the'
    " PDF with `check print-pdf --run`. A person's act.",
    m.CheckReprintInput, m.CheckPrintWriteOutput, plan_reprint, p.apply_reprint,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_DUPLICATE_NUMBER', 'E_PERIOD_CLOSED', 'E_VERSION_CONFLICT',
            'E_VALUE_RANGE', 'E_PERMISSION', *_TAIL], positional=['check'])

check_print_run = _read(
    'check print-run', 'Show a print run: its account, style, first number, alignment, each cheque with the number it'
    ' took and what became of it (pending, printed, failed, cancelled), and the numbers it left spoiled.',
    m.CheckPrintRunInput, m.CheckPrintRunOutput, plan_print_run, errors=['E_RECORD_NOT_FOUND'], positional=['run'])

check_print_pdf = _read(
    'check print-pdf', 'The PDF of a print run, laid out for preprinted check stock with the alignment the run used:'
    ' payee, date, amount in figures and words, address window and memo, and for voucher checks two stubs listing the'
    ' bills paid (with discounts) or the expense lines. Returned base64-encoded; the CLI saves it as `filename`.',
    m.CheckPrintRunInput, m.CheckPdfOutput, plan_print_pdf, errors=['E_RECORD_NOT_FOUND'], positional=['run'])

check_print_setup = _write(
    'check print-setup', "Set a bank account's check printer setup: the default `style` and the fine alignment, in"
    ' hundredths of an inch (horizontal: positive moves right; vertical: positive moves up). Try offsets first with'
    ' `check print-alignment`.',
    m.CheckPrintSetupInput, m.CheckPrintSetupOutput, plan_print_setup, p.apply_setup,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_VERSION_CONFLICT', *_TAIL])

check_print_alignment = _read(
    'check print-alignment', "Print an alignment sample: one check of made-up figures, marked as a sample, at the"
    " account's saved offsets or at offsets being tried, in any style and on a partial first sheet.",
    m.CheckPrintAlignmentInput, m.CheckPdfOutput, plan_print_alignment, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
