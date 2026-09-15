"""The hosted producer stays on the session for the whole publication lifecycle.

``run_hosted`` hands the authenticated producer to the session because publication
capture re-asks the company's own resource requirements, and an activated policy
admits those through that producer rather than through an OS login a host does not
have. So the credential has to still be on the session when the permit is finished
-- on the successful path exactly as much as on the failed one -- and it has to be
gone once the call returns, however it returned.

Cleared too early, every hosted read whose capture consults company permissions
answers ``E_IO {stage: publication, reason: receipt_certificate}`` instead of its
result. The two ordering witnesses below do not need an activated policy: they name
the ordering itself, so the guarantee cannot regress quietly behind a policy mode.
"""
import pytest

from bookflow.core.publication import PublicationPermit
from tests.test_bill_item_lines import books
from tests.test_purchase_deletion_http import office
from tests.test_payment_deletion_journey import PAYMENT_DELETE, activate, unapplied_receipt

MISSING = '01JZZZZZZZZZZZZZZZZZZZZZZZ'


@pytest.fixture
def finishes(monkeypatch):
    """Every permit finish this request makes, with the session's credential as it stood."""
    seen = []
    original = PublicationPermit.finish

    def recorded(self, s, *, succeeded=True, **values):
        seen.append((self.cmd.name, succeeded, s.credential))
        return original(self, s, succeeded=succeeded, **values)

    monkeypatch.setattr(PublicationPermit, 'finish', recorded)
    return seen


def test_the_permit_is_finished_while_the_credential_is_still_live_on_both_paths(books, office, finishes):
    """The success path is the one that regressed; the failure path proves the pair."""
    company = books['company']
    answered = office.admin('payment.query', {}, company=company)
    assert answered['items'] == [] and answered['total_count'] == 0, answered
    refused = office.call(office.installer, 'payment.show', dict(payment=MISSING), company=company)
    assert refused.json()['code'] == 'E_RECORD_NOT_FOUND', refused.text

    outcomes = {(name, succeeded): credential for name, succeeded, credential in finishes}
    assert ('payment query', True) in outcomes, finishes
    assert ('payment show', False) in outcomes, finishes
    cleared = sorted(key for key, credential in outcomes.items() if credential is None)
    assert not cleared, f'the session credential was cleared before these permits were finished: {cleared}'


def test_the_session_credential_is_cleared_even_when_finishing_raises(books, office, monkeypatch):
    """The cleanup guarantee survives a publication that fails inside finish itself."""
    company = books['company']
    sessions = []

    def explode(self, s, **values):
        sessions.append(s)
        raise RuntimeError('the receipt certificate could not be written')

    monkeypatch.setattr(PublicationPermit, 'finish', explode)
    answered = office.call(office.installer, 'payment.query', {}, company=company)
    assert answered.status_code != 200, answered.text
    assert sessions, 'the permit was never finished, so this witness proved nothing'
    assert all(s.credential is None for s in sessions), 'a hosted session kept its credential'


def test_an_activated_host_answers_payment_reads_and_omits_a_deleted_receipt(books, office):
    """The live surface: every one of these reads makes capture re-ask company permissions."""
    company = books['company']
    payment, _ = unapplied_receipt(office, books)
    activate(office)

    listed = office.admin('payment.query', {}, company=company)
    assert [row['id'] for row in listed['items']] == [payment], listed
    candidates = office.admin('payment.invoices', dict(mode='new_receipt', customer=books['customer'],
                                                       date='2017-01-02'), company=company)
    assert candidates['customer_id'] == books['customer'], candidates

    person = office.admin('user.add', dict(username='lifecycle-deleter', password='deleter fixture',
                                           company=company, role='standard'))
    office.admin('membership.grant', dict(user=person['user_id'], company=company, expected_version=1,
                                          grants=[PAYMENT_DELETE], denies=['ledger.post']))
    clerk = office.login_as('lifecycle-deleter', 'deleter fixture')
    current = office.admin('payment.show', dict(payment=payment), company=company)
    deleted = office.ok(clerk, 'payment.delete', dict(payment=payment, expected_version=current['version'],
        operation_key='lifecycle-delete'), company=company,
        headers={'X-Bookflow-Reason': 'Remove duplicate receipt'})
    assert deleted['status'] == 'deleted'

    # The ordinary list is the read a person actually opens, and a deleted receipt
    # is not in it. Only an explicit include_deleted read brings its facts back.
    after = office.admin('payment.query', {}, company=company)
    assert [row['id'] for row in after['items']] == [], after
    retained = office.admin('payment.query', dict(include_deleted=True), company=company)
    assert [row['id'] for row in retained['items']] == [payment], retained
    # Not only the installer: an ordinary member reads the same activated host.
    assert office.ok(clerk, 'payment.query', {}, company=company)['items'] == []
