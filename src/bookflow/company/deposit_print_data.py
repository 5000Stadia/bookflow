"""Complete typed print data from one snapshot; rendering remains separately owned."""
from bookflow.company import deposit_read_models as m, deposit_read_facts as facts, deposit_read_pages as pages
from bookflow.company import deposit_queries as queries, deposit_read_authority as authority


def print_data(s,inp,*,binding):
    inp=queries.checked(inp,m.PrintDataInput);authority.selected(s,inp.deposit,binding=binding)
    data=facts.load_complete(s,[inp.deposit],binding=binding)[0]
    return assemble(s,data,inp,binding=binding)


def assemble(s,data,inp,*,binding,with_guard=True):
    """Assemble only an already admitted complete snapshot; no independent load."""
    document=queries._show(s,data,m.ShowInput(**inp.model_dump(exclude_unset=True)),binding,with_guard=with_guard)
    groups=queries.collections(data,document.selected.pin)
    rows=tuple(sorted((*groups['sources'],*groups['additional']),key=lambda r:(r.captured.ordinal,r.captured.row_id) if isinstance(r,m.SourceItem) else (r.ordinal,r.row_id)))
    fp=pages.fingerprint(s,binding,'print',[document.selected.model_dump(mode='json'),{k:queries.immutable(v) for k,v in groups.items()},document.totals.model_dump(mode='json')])
    return m.DepositPrintData(document=document,rows=rows,cash_allocations=groups['cash_allocations'],snapshot_reference=fp,generated_at=queries.now())
