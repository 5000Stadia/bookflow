"""A publication failure names what actually failed.

``run_hosted`` finishes the publication permit after the command executes, and one broad
``except Exception`` around that finish answered *every* failure -- a typed ``BookflowError``
the permit itself had raised included -- as ``E_IO {reason: receipt_certificate}``, whose
message reads "A filesystem operation failed."

That is not a cosmetic complaint. Taking a customer's payment for a statement charge failed
over every published transport while working in process, and what the person was told was
that their disk had failed. The real cause was a receivable the publication root capture
could not resolve. The symptom named the wrong subsystem, and that is exactly why nobody
found the defect by reading the error.

So: a typed error is already this product's public contract -- a stable code and typed
details, the same answer the command gives on every other surface -- and is answered as
itself. An untyped one still gets the generic certificate failure, because it can carry
internals across a boundary that must not describe what it could not certify; its exact
cause and traceback go to the server log instead, where the operator reads them and the
caller never does.
"""
import logging

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.publication import PublicationPermit
from tests.test_bill_item_lines import books
from tests.test_purchase_deletion_http import office


def _finish_raises(monkeypatch, error):
    """Every permit finish in the request fails the same way, whatever it was finishing."""

    def finish(self, s, **values):
        raise error

    monkeypatch.setattr(PublicationPermit, 'finish', finish)


def test_a_typed_publication_failure_reports_its_own_cause(books, office, monkeypatch):
    """The defect's own shape: a record the capture could not resolve, over the host."""
    _finish_raises(monkeypatch, BookflowError(
        'E_RECORD_NOT_FOUND', details={'record_type': 'invoice', 'selector': 'SC-1'}))

    answered = office.call(office.installer, 'payment.query', {}, company=books['company'])
    body = answered.json()

    assert body['code'] == 'E_RECORD_NOT_FOUND', (
        '\n  A publication that failed on a record it could not resolve answered'
        f"\n  {body['code']} instead of naming what failed."
        '\n  Reporting it as a filesystem error tells a person whose settlement was'
        '\n  refused that their disk failed, and sends whoever reads the symptom to'
        f'\n  the wrong subsystem.\n  The response was: {answered.text[:400]}\n')
    assert body['details'].get('selector') == 'SC-1', body
    assert 'filesystem' not in body['message'].lower(), body


def test_an_untyped_publication_failure_stays_generic_and_is_logged_exactly(
        books, office, monkeypatch, caplog):
    """The protection the broad catch is genuinely for, with the cause made discoverable."""
    _finish_raises(monkeypatch, RuntimeError('the receipt certificate could not be written'))

    with caplog.at_level(logging.ERROR, logger='bookflow.http'):
        answered = office.call(office.installer, 'payment.query', {}, company=books['company'])
    body = answered.json()

    assert body['code'] == 'E_IO', answered.text
    assert body['details']['reason'] == 'receipt_certificate', body
    # Nothing of the internal failure reaches the caller.
    assert 'receipt certificate could not be written' not in answered.text, answered.text

    recorded = [record for record in caplog.records
                if record.name == 'bookflow.http' and record.exc_info is not None]
    assert recorded, (
        '\n  An untyped publication failure was fenced off from the caller and then'
        '\n  left nowhere at all: no server-log record carries the cause, so the only'
        '\n  account of what went wrong is the word "filesystem", which is wrong.\n'
        f'  bookflow.http records seen: {[r.getMessage() for r in caplog.records]}\n')
    cause = recorded[-1].exc_info[1]
    assert isinstance(cause, RuntimeError), recorded[-1].exc_info
    assert 'receipt certificate could not be written' in str(cause), str(cause)
