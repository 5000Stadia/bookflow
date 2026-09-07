"""Combined-source SDK/CLI/HTTP/Python list discovery and complete paging."""
import json
import os
import sqlite3
from pathlib import Path
import anyio
import pytest
from tests.mcp_matrix_support import Matrix
from bookflow.company.lists import LIST_DEFINITIONS
from bookflow.company.query_projection import COLLECTIONS


def company_snapshot(root, company):
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
        relative = db.execute('SELECT path FROM companies WHERE id=?', (company,)).fetchone()[0]
    with sqlite3.connect((root/relative/'company.db').as_uri()+'?mode=ro', uri=True) as db:
        return '\n'.join(db.iterdump())


@pytest.mark.parametrize("noun", tuple(LIST_DEFINITIONS))
def test_list_browsing_four_interfaces(root, tmp_path, monkeypatch, noun):
    # An explicit candidate executable pins both subprocess adapters.
    if binary := os.environ.get('BOOKFLOW_MCP_TEST_BINARY'):
        monkeypatch.setattr('tests.conftest.BIN', Path(binary))
    async def witness():
        matrix = Matrix()
        receipts = {}
        try:
            await matrix.open(root, tmp_path/'surfaces')
            before = {s: company_snapshot(r, matrix.company) for s,r in matrix.roots.items()}
            calls = []
            for selected_noun in (noun,):
                calls.append((noun+' query options', {'kind':'columns','limit':50}))
                calls.append((noun+' query', {'projection':'reference','limit':2,'include_inactive':True}))
            for owner,column in COLLECTIONS:
                if owner != noun:
                    continue
                record = await matrix.call('python', noun+' query', {'limit':1,'include_inactive':True})
                assert record['items'], noun
                calls.append((noun+' query children', {'record':record['items'][0]['id'],'column':column,'limit':1}))
            # Setup reads are excluded from equality; all command results below are compared exactly.
            for s in matrix.documents: matrix.documents[s].clear()
            for s in matrix.documents:
                observed=[]
                for name, raw in calls:
                    page = await matrix.call(s,name,raw)
                    chain=[page]
                    seen=set()
                    while page.get('next_cursor'):
                        cursor=page['next_cursor']
                        assert cursor not in seen
                        seen.add(cursor)
                        page=await matrix.call(s,name,dict(raw,cursor=cursor))
                        chain.append(page)
                    observed.append((name,raw,chain))
                    if name.endswith(' query options'):
                        defaults=chain[0]['default_columns']
                        selected=await matrix.call(s,name[:-8],{'columns':defaults,'limit':2})
                        observed.append((name[:-8],{'columns':defaults,'limit':2},[selected]))
                receipts[s]=observed
                assert company_snapshot(matrix.roots[s],matrix.company)==before[s],s
            for s in ('cli','http','mcp'):
                assert receipts[s]==receipts['python'],s
            (tmp_path/'four-interface-list-receipts.json').write_text(json.dumps(receipts,indent=2))
        finally:
            if hasattr(matrix, "stack"):
                await matrix.close()
    anyio.run(witness)
