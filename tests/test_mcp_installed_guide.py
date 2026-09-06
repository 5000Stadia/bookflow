"""Extract the installed documentation's literal program; never patch its body."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_row3_host import hosted, live
from tests.test_agent_guide import classified_fences


@pytest.mark.timeout(180)
def test_literal_installed_mcp_guide(hosted, live, tmp_path):
    binary = Path(os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow'))))
    python = binary.with_name('python')
    extracted = subprocess.run([str(python), '-c',
        'from importlib.resources import files; print(files("bookflow.documentation").joinpath("resources/mcp-guide.md").read_text(), end="")'],
        capture_output=True, text=True, check=True, cwd=tmp_path)
    blocks = classified_fences(extracted.stdout)
    assert len(blocks) == 1 and blocks[0].classification == 'executable' and blocks[0].language == 'python'
    literal = blocks[0].body.encode()
    script = tmp_path / 'literal-installed-guide.py'
    script.write_bytes(literal)
    inbox, outbox = tmp_path / 'inbox', tmp_path / 'outbox'
    inbox.mkdir(mode=0o700)
    outbox.mkdir(mode=0o700)
    receipt = inbox / 'trial-receipt.pdf'
    receipt.write_bytes(b'%PDF-1.4\n' + b'Trial receipt\n' * 1000 + b'%%EOF\n')
    env = {**os.environ, 'BOOKFLOW_URL': live, 'BOOKFLOW_TOKEN': hosted.secret,
           'BOOKFLOW_COMPANY': hosted.company_id, 'BOOKFLOW_RECEIPT': str(receipt),
           'BOOKFLOW_MCP_OUTDIR': str(outbox), 'BOOKFLOW_MCP_BINARY': str(binary),
           'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent-caller-root')}
    executed = subprocess.run([str(python), str(script)], env=env, cwd=tmp_path,
                              capture_output=True, text=True, timeout=150)
    assert executed.returncode == 0, executed.stderr
    assert script.read_bytes() == literal
    print('LITERAL GUIDE SHA256', hashlib.sha256(literal).hexdigest())
    result = json.loads(executed.stdout)
    assert result['company'] == hosted.company_id
    invoice = hosted.ok('invoice.show', {'invoice': result['invoice']}, company=hosted.company_id)
    assert invoice['version'] == 2 and invoice['total_minor_units'] == 617
    journal = hosted.ok('journal.show', {'journal': result['journal']}, company=hosted.company_id)
    assert journal['total_minor_units'] == 1234
    events = hosted.ok('audit.list', {'command': 'journal post'}, company=hosted.company_id)['items']
    event = next(row for row in events if row['client_name'] == 'installed-mcp-guide')
    assert event['interface'] == 'mcp' and event['directive_code'] == result['directive']
    assert not (tmp_path / 'absent-caller-root').exists()
