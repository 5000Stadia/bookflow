"""Advisory presence is shared state, with no financial/audit write or preview."""
import re
import sqlite3
import anyio
import pytest
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import company_snapshot, GHOST

COMMANDS = frozenset(('presence set','presence clear'))


@pytest.mark.timeout(180)
def test_advisory_presence_exact_documents_and_no_business_mutation(root,tmp_path):
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b',line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root,tmp_path)
            for surface in matrix.documents:
                before = company_snapshot(matrix.roots[surface])
                target = dict(record_type='company_info',record_id=matrix.company)
                shown = await matrix.call(surface,'presence set',target)
                assert len(shown['editing_by']) == 1 and shown['editing_by'][0]['interface'] == surface
                cleared = await matrix.call(surface,'presence clear',target)
                assert cleared['editing_by'] == []
                after = company_snapshot(matrix.roots[surface])
                def without_seen_stamp(rows):
                    result = []
                    for row in rows:
                        if row.startswith('INSERT INTO "principals" VALUES('):
                            row, changed = re.subn(r",'\d{4}-\d\d-\d\dT[^']+'\);$", ",'<principal-last-seen>');", row)
                            assert changed == 1
                        result.append(row)
                    return result
                # Successful advisory calls refresh cached principal liveness;
                # preserve every other column/table, including all audit rows.
                assert without_seen_stamp(after) == without_seen_stamp(before)
                before = after
                for name in sorted(COMMANDS):
                    for data,ctx,code in [(target,{'dry_run':True},'E_USAGE'),
                        (target,{'company':GHOST},'E_COMPANY_NOT_FOUND'),
                        ({**target,'record_id':GHOST},{},'E_RECORD_NOT_FOUND')]:
                        assert (await matrix.call(surface,name,data,rejected=True,**ctx))['code'] == code
                        assert company_snapshot(matrix.roots[surface]) == before
            expected = normalize(matrix.documents['python'],matrix.roots['python'],baseline_ids)
            for surface in ('cli','http','mcp'):
                actual = normalize(matrix.documents[surface],matrix.roots[surface],baseline_ids)
                assert actual == expected
        finally:
            await matrix.close()
    anyio.run(witness)
