"""Complete report print data from one admitted snapshot, without rendering.

Captured compositions reuse the accepted print owner; no repeated page reads,
attachment capabilities, output device, or public activation.
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


def print_data(s,inp,*,binding):
    from bookflow.company import deposit_report_models as m, deposit_reports as reports
    from bookflow.company import deposit_queries as queries, deposit_read_pages as pages
    from bookflow.company import deposit_print_data as printing, deposit_read_models as read
    inp=queries.checked(inp,m.DepositDetailFilter)
    rows,totals,uf,evidence,content,lookup=reports._runtime(s,inp,binding=binding)
    fp=pages.fingerprint(s,binding,'report.detail',content)
    compositions=tuple(printing.assemble(s,lookup[r.movement.transaction_id],read.PrintDataInput(
        deposit=r.movement.transaction_id,revision_number=r.selected.pin.revision_number),binding=binding,with_guard=False) for r in rows)
    return m.DepositReportPrintData(metadata=reports._metadata(s,inp,fp),rows=rows,compositions=compositions,totals=totals,uf=uf,evidence=evidence)
