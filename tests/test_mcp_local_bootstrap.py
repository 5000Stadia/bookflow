"""Installed CLI and Python bootstrap share their machine-local lifecycle."""
import json
import os
from pathlib import Path
import subprocess
import sys
import bookflow
from tests.mcp_matrix_support import normalize


def test_installed_cli_bootstrap_preview_create_reopen_and_conflict(tmp_path):
    results = {}
    roots = {}
    for surface in ('python', 'cli'):
        root = tmp_path/surface
        roots[surface] = root
        client = bookflow.connect(data_root=str(root))
        def call(raw, *, preview=False, rejected=False):
            if surface == 'python':
                try:
                    doc = client.run('init',raw,dry_run=preview)
                    assert not rejected
                except bookflow.BookflowError as exc:
                    assert rejected
                    doc = exc.to_dict()
            else:
                binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY',str(Path(sys.executable).with_name('bookflow')))
                args = [binary,'init','--json',*(['--dry-run'] if preview else [])]
                for key,value in raw.items():
                    args.extend(['--'+key.replace('_','-'),value])
                result = subprocess.run(args,env={**os.environ,'BOOKFLOW_DATA_ROOT':str(root)},capture_output=True,text=True,timeout=30)
                assert (result.returncode != 0) == rejected,(result.stdout,result.stderr)
                doc = json.loads(result.stderr.strip().splitlines()[-1] if rejected else result.stdout)
            results.setdefault(surface,[]).append(doc)
            return doc
        raw = {'username':'owned-bootstrap','display_name':'Owned bootstrap'}
        assert call(raw,preview=True)['dry_run'] and not root.exists()
        created = call(raw)
        assert created['created'] and (root/'hub.db').is_file()
        reopened = call({})
        assert not reopened['created'] and reopened['user_id'] == created['user_id']
        assert call({'username':'conflicting-bootstrap'},rejected=True)['code'] == 'E_INIT_CONFLICT'
        events = client.hub.audit.list(command='init')['items']
        assert len(events) == 1 and events[0]['interface'] == surface
    assert normalize(results['python'],roots['python'],set()) == normalize(results['cli'],roots['cli'],set())
