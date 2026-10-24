"""Several bad rows on one inline deposit report the first bad row, with base's code (R153).

Batched source reads must not change which refusal a caller sees: a stale expected version on
the first row comes before an unknown receipt on the second, exactly as when rows were read one
at a time.
"""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_deposit_command import COMPANY, books, receipts_in_undeposited_funds  # noqa: F401


def test_first_bad_row_in_row_order_is_reported(books):
    client = books["client"]
    _, payment, sale = receipts_in_undeposited_funds(books)

    def post(sources):
        return client.run("deposit post", dict(operation_key=new_id(), document=dict(
            mode="inline", deposit_to=books["bank"], date="2026-06-03", sources=sources)), company=COMPANY)

    stale = dict(source=payment["id"], source_type="payment", expected_version=payment["version"] + 5)
    unknown = dict(source=new_id(), source_type="payment", expected_version=1)
    with pytest.raises(BookflowError) as first:
        post([stale, unknown])
    assert first.value.code == "E_VERSION_CONFLICT"
    with pytest.raises(BookflowError) as second:
        post([unknown, stale])
    assert second.value.code == "E_RECORD_NOT_FOUND"
    good = dict(source=sale["id"], source_type="sales_receipt", expected_version=sale["version"])
    with pytest.raises(BookflowError) as third:
        post([good, stale, unknown])
    assert third.value.code == "E_VERSION_CONFLICT"
