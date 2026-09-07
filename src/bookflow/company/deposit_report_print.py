"""Pure complete-relation print arithmetic, pending read-owner print supplier.

This is not DepositReportPrintData: captured custom values, issuer/operation
links, authority/evidence and company metadata must come from the real reader.
No paginated reader loop or synthetic metadata stands in for that dependency.
"""
from bookflow.company.deposit_report_models import CompleteRelation, ReportRevision, ReportDeposit
from bookflow.core.errors import BookflowError


def selected_compositions(relation: CompleteRelation,
                          deposits: tuple[ReportDeposit, ...]) -> tuple[ReportRevision, ...]:
    """Selected captured compositions, one per report row, including inverses.

    Row monetary sign stays in the report row; the captured business document
    remains positive and unmodified. Caller must use the same validated snapshot.
    """
    index = {}
    for deposit in deposits:
        for revision in deposit.revisions:
            key = deposit.id, revision.revision_id
            if key in index:
                raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
            index[key] = revision
    result = []
    for row in relation.rows:
        revision = index.get((row.transaction_id, row.revision_id))
        if revision is None or revision.number != row.number or revision.effect.intent.bank.id != row.destination_id:
            raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
        result.append(revision)
    return tuple(result)
