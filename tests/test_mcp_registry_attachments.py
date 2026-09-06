"""Registered binary transport parity keeps bytes, business metadata and ownership."""
from copy import deepcopy
import hashlib
import io
import json
import re
import sqlite3
import anyio
import pytest
import bookflow
from bookflow.core.transfer_protocol import encode_input, decode_input
from tests.conftest import Cli
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import company_snapshot, GHOST

COMMANDS = frozenset('attachment '+verb for verb in ('add','get','link','list','unlink'))
BODY = b'%PDF-1.4\n'+bytes(range(256))*8192+b'\n%%EOF\n'


@pytest.mark.timeout(240)
def test_registered_binary_and_link_lifecycle_complete_parity(root,client,tmp_path):
    company = client.company.list()['items'][0]['company_id']
    targets = [client.customer.create(name='Binary matrix '+name,company=company)['id'] for name in ('original','linked')]
    inbox,outbox = tmp_path/'inbox',tmp_path/'outbox'
    inbox.mkdir(mode=0o700);outbox.mkdir(mode=0o700)
    source = inbox/'Receipt é.pdf';source.write_bytes(BODY)
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b',line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root,tmp_path,mcp_args=['--input-dir',str(inbox),'--output-dir',str(outbox)])
            for surface in matrix.documents:
                async def transfer(name,raw,*,rejected=False,dry_run=False,selected=None,key=None):
                    selection = selected or matrix.company
                    before = company_snapshot(matrix.roots[surface]) if rejected or dry_run else None
                    options = dict(company=selection,dry_run=dry_run)
                    if name == 'attachment add': options['reason'] = 'Registry parity'
                    if key: options['idempotency_key'] = key
                    output = outbox/(surface+'-'+str(len(matrix.documents[surface]))+'.pdf')
                    if surface == 'python':
                        sink = io.BytesIO()
                        try:
                            document = bookflow.connect(data_root=str(matrix.roots[surface])).run(name,raw,**options,
                                **({'input_stream':io.BytesIO(BODY)} if name.endswith('add') else {'output_stream':sink}))
                            error = False
                            if name.endswith('get'): assert sink.getvalue() == BODY
                        except bookflow.BookflowError as exc:
                            document,error = exc.to_dict(),True
                    elif surface == 'cli':
                        args = ['--company',selection]
                        if dry_run: args.append('--dry-run')
                        if key: args.extend(['--idempotency-key',key])
                        if name.endswith('add'):
                            args.extend(['--reason','Registry parity','attachment','add',raw['record_type'],raw['record_id'],str(source)])
                            for field in ('original_filename','media_type','caption'):
                                args.extend(['--'+field.replace('_','-'),raw[field]])
                        else: args.extend(['attachment','get',raw['attachment'],'--out',str(output)])
                        done = Cli(matrix.roots[surface]).run(*args,'--json',expect=None)
                        error = done.returncode != 0
                        document = json.loads(done.stderr.strip().splitlines()[-1] if error else done.stdout)
                        if not error and name.endswith('get'): assert output.read_bytes() == BODY
                    elif surface == 'http':
                        hosted = matrix.hosts[surface]
                        headers = {**hosted.bearer,'X-Bookflow-Input':encode_input(raw),'Content-Type':'application/octet-stream'}
                        if name.endswith('add'): headers['X-Bookflow-Reason'] = 'Registry parity'
                        if key: headers['Idempotency-Key'] = key
                        chunks = (BODY[start:start+65536] for start in range(0,len(BODY),65536)) if name.endswith('add') else b''
                        reply = hosted.api.post('/companies/'+selection+'/transfers/'+name.replace(' ','.'),
                            params={'dry_run':str(dry_run).lower()},headers=headers,content=chunks)
                        error = reply.status_code >= 400
                        if not error and name.endswith('get'):
                            assert reply.content == BODY
                            document = decode_input(reply.headers['X-Bookflow-Output'])
                        else: document = reply.json()
                    else:
                        reply = await matrix.mcp.call_tool('bookflow_run',{'command':name,'input':raw,**options,
                            'transport':{'input_file':str(source)} if name.endswith('add') else {'output_file':str(output)}})
                        error,document = reply.is_error,reply.structured_content
                        if not error and name.endswith('get'):
                            assert output.read_bytes() == BODY
                            assert reply.meta['bookflow_transport']['output_file'] == str(output)
                    assert bool(error) == rejected,(surface,name,document)
                    if before is not None: assert company_snapshot(matrix.roots[surface]) == before
                    matrix.documents[surface].append((name,deepcopy(document)))
                    return document
                raw = dict(record_type='customer',record_id=targets[0],original_filename='Preserved receipt é.pdf',
                           media_type='application/pdf',caption='Paid service <receipt>')
                assert (await transfer('attachment add',raw,dry_run=True))['dry_run']
                added = await transfer('attachment add',raw,key='binary-once')
                attachment = added['attachment']
                assert attachment['size_bytes'] == len(BODY) and attachment['sha256'] == hashlib.sha256(BODY).hexdigest()
                assert attachment['original_filename'] == raw['original_filename']
                assert attachment['created_via'] == surface
                replay = await transfer('attachment add',raw,key='binary-once')
                assert replay['attachment'] == attachment and replay['idempotent_replay']
                assert await transfer('attachment get',dict(attachment=attachment['id'])) == attachment
                assert (await transfer('attachment get',dict(attachment=GHOST),rejected=True))['code'] == 'E_RECORD_NOT_FOUND'
                assert (await transfer('attachment add',raw,selected=GHOST,rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                link_input = dict(attachment=attachment['id'],record_type='customer',record_id=targets[1],caption='Second business association')
                assert (await matrix.call(surface,'attachment link',link_input,dry_run=True))['dry_run']
                linked = await matrix.call(surface,'attachment link',link_input)
                assert linked['link']['created_via'] == surface
                page = await matrix.call(surface,'attachment list',dict(record_type='customer',record_id=targets[1],limit=200))
                assert page['count'] == 1
                unlink = dict(link=linked['link']['id'],expected_version=1)
                assert (await matrix.call(surface,'attachment unlink',unlink,dry_run=True))['dry_run']
                removed = await matrix.call(surface,'attachment unlink',unlink)
                assert not removed['link']['active']
                before = company_snapshot(matrix.roots[surface])
                assert (await matrix.call(surface,'attachment unlink',unlink,rejected=True))['code'] == 'E_VERSION_CONFLICT'
                for name,data in [('attachment link',link_input),('attachment list',dict(record_type='customer',record_id=targets[1])),('attachment unlink',unlink)]:
                    assert (await matrix.call(surface,name,data,company=GHOST,rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                assert company_snapshot(matrix.roots[surface]) == before
                assert {name for name,_ in matrix.documents[surface]} == COMMANDS
                assert not matrix.hosts.get(surface) or not matrix.hosts[surface].handle.host._transfers
            expected = normalize(matrix.documents['python'],matrix.roots['python'],baseline_ids)
            for surface in ('cli','http','mcp'):
                actual = normalize(matrix.documents[surface],matrix.roots[surface],baseline_ids)
                assert len(actual) == len(expected)
                for index,(a,b) in enumerate(zip(expected,actual)):
                    assert a == b,(surface,index,a,b)
        finally:
            await matrix.close()
    anyio.run(witness)
