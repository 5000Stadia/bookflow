"""Four real adapters over identical private seed copies for parity scenarios."""

from contextlib import AsyncExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import sys

import bookflow
from bookflow.adapters.cli.app import _flatten, _leaf_type
from bookflow.commands.host_cmds import start_serving
from bookflow.core import registry
from bookflow.core.context import client_version
from tests.conftest import Cli
from tests.test_row3_host import Hosted, live


class Matrix:
    async def open(self, baseline, directory):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        seed = bookflow.connect(data_root=str(baseline))
        self.company = seed.company.list()['items'][0]['company_id']
        issued = seed.token.issue(label='Parity bearer')
        self.stack = AsyncExitStack()
        self.roots = {}
        self.hosts = {}
        self.documents = {surface: [] for surface in ('python', 'cli', 'http', 'mcp')}
        for surface in self.documents:
            root = directory / surface
            shutil.copytree(baseline, root)
            self.roots[surface] = root
            if surface in ('http', 'mcp'):
                handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
                self.stack.callback(handle.stop)
                self.hosts[surface] = Hosted(handle, root, '', self.company, issued, '', {})
        generator = live.__wrapped__(self.hosts['mcp'])
        url = next(generator)
        self.stack.callback(generator.close)
        binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))
        read, write = await self.stack.enter_async_context(stdio_client(StdioServerParameters(
            command=binary, args=['mcp', '--url', url], cwd=str(directory),
            env={'BOOKFLOW_TOKEN': issued['secret'], 'BOOKFLOW_COMPANY': self.company,
                 'BOOKFLOW_DATA_ROOT': str(directory / 'absent')})))
        self.mcp = await self.stack.enter_async_context(ClientSession(read, write))
        await self.mcp.discover()
        return self

    async def close(self):
        await self.stack.aclose()

    async def call(self, surface, command, raw, *, rejected=False, **context):
        cmd = registry.get(command)
        options = {**({'company': self.company} if cmd.scope == 'company' else {}), **({'reason': 'Registry parity'} if cmd.is_write else {}), **context}
        if surface == 'python':
            try:
                document = bookflow.connect(data_root=str(self.roots[surface])).run(command, raw, **options)
                error = False
            except bookflow.BookflowError as exc:
                document, error = exc.to_dict(), True
        elif surface == 'cli':
            args = ['--company', options['company']] if 'company' in options else []
            for key, value in options.items():
                if key == 'company':
                    continue
                if isinstance(value, bool):
                    if value:
                        args.append('--' + key.replace('_', '-'))
                else:
                    args.extend(['--' + key.replace('_', '-'), str(value)])
            args.extend(command.split())
            leaves = _flatten(cmd.input_model)
            leaves.sort(key=lambda leaf: cmd.positional.index(leaf[0]) if leaf[0] in cmd.positional else len(cmd.positional))
            # A discriminated request can have a collection at a path in one
            # branch and nested model controls at that path in another. Emit the
            # chosen input's nested controls, not the inactive collection flag.
            model_paths = {leaf[0] for leaf in leaves}
            for path, flag, annotation, _, _, _ in leaves:
                value = raw
                for part in path.split('.'):
                    if not isinstance(value, dict) or part not in value:
                        break
                    value = value[part]
                else:
                    if isinstance(value, dict) and any(other.startswith(path + '.') for other in model_paths):
                        continue
                    metadata = cmd.input_model.model_fields.get(path)
                    if metadata and isinstance(metadata.json_schema_extra, dict):
                        flag = metadata.json_schema_extra.get('cli_flag', flag)
                    if value is None and cmd.clearable:
                        args.extend(['--clear', path])
                    elif isinstance(value, bool) and _leaf_type(annotation)[0] is bool:
                        args.append('--' + ('' if value else 'no-') + flag)
                    else:
                        if path not in cmd.positional:
                            args.append('--' + flag)
                        args.append(json.dumps(value) if isinstance(value, (list, dict, bool)) or value is None else str(value))
            completed = Cli(self.roots[surface]).run(*args, '--json', expect=None)
            error = completed.returncode != 0
            document = json.loads((completed.stderr.strip().splitlines()[-1] if error else completed.stdout))
        elif surface == 'http':
            host = self.hosts[surface]
            company = options.pop('company', None)
            dry_run = options.pop('dry_run', False)
            response = host.api.post(('/companies/' + company if company else '') + '/commands/' + command.replace(' ', '.'),
                json=raw, params={'dry_run': str(dry_run).lower()},
                headers={**host.bearer, **{('Idempotency-Key' if key == 'idempotency_key' else 'X-Bookflow-' + key.replace('_', '-')): value
                         for key, value in options.items()}})
            error, document = response.status_code >= 400, response.json()
        else:
            reply = await self.mcp.call_tool('bookflow_run', {'command': command, 'input': raw, **options})
            error, document = reply.is_error, reply.structured_content
        assert bool(error) == rejected, (surface, command, document)
        self.documents[surface].append((command, deepcopy(document)))
        return document


def normalize(documents, root, baseline_ids):
    ids = {}
    def visit(value, key=None):
        if isinstance(value, dict):
            if value.get('code') == 'E_DIRECTIVE_INACTIVE':
                stamp = value['details']['deactivated_at']
                assert stamp in value['message']
                value = {**value, 'message': value['message'].replace(stamp, '<timestamp>')}
            if value.get('code') == 'E_VERSION_CONFLICT':
                value = {**value, 'message': re.sub(r'[0-9.]+ s ago', '<elapsed> s ago', value['message'])}
            return {k: visit(v, k) for k, v in sorted(value.items())}
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, tuple):
            return tuple(visit(v) for v in value)
        if key == 'facts_fingerprint' and value is not None:
            assert isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
            # Sales hashes include this clone's generated transaction/line IDs.
            # Each scenario submits its exact preview hash before comparing.
            return '<generated-facts-fingerprint>'
        if key == 'seconds_since_update':
            assert isinstance(value, (int, float)) and value >= 0
            return '<elapsed>'
        if not isinstance(value, str):
            return value
        if key in ('created_via', 'updated_via', 'interface') and value in ('cli', 'python', 'http', 'mcp'):
            return '<interface>'
        if re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:.*', value):
            return '<timestamp>'
        if re.fullmatch(r'[0-9A-HJKMNP-TV-Z]{26}', value) and value not in baseline_ids:
            return ids.setdefault(value, '<generated-' + str(len(ids)) + '>')
        return value.replace(str(root), '<root>')
    return visit(documents)
