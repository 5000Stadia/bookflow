"""Delivered file/intent client. Models supply business inputs and permitted paths."""

import hashlib
import io
import json
from contextlib import ExitStack

import anyio

from bookflow.core.errors import BookflowError
from .catalog import BRIDGE_VERSION
from .envelopes import RunArguments, intent_reference
from .framing import Decoder, invalid, json_chunks


class Client:
    def __init__(self, http, inputs, outputs):
        self.http, self.inputs, self.outputs = http, inputs, outputs
        from .inspection import Mappings
        from .limits import json_seconds
        self.json_seconds = json_seconds()
        self.mappings = Mappings()

    def document(self, response, reference=None):
        try:
            if response.headers.get("x-bookflow-mcp-version") != str(BRIDGE_VERSION):
                raise ValueError
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            if response.status_code >= 400:
                if (set(value) != {"code", "message", "details"} or not isinstance(value['details'], dict)
                        or not isinstance(value['code'], str) or not value['code'].startswith('E_')
                        or not isinstance(value['message'], str)):
                    raise ValueError
                error = BookflowError(value['code'], message=value['message'], details=value['details'])
                error.core_document = True
                raise error
            return value
        except (ValueError, KeyError, TypeError):
            raise invalid("invalid_response") from None

    async def post(self, path, **kwargs):
        return self.document(await self.http.post(path, **kwargs))

    async def source(self, path, digest=None):
        # Leaving the descriptor context verifies inode/size/timestamps/lineage
        # before HTTP emits the terminating body marker and permits execution.
        with self.inputs.input(path) as stream:
            while True:
                chunk = await anyio.to_thread.run_sync(stream.read, 65536)
                if not chunk:
                    break
                if digest is not None:
                    digest[0].update(chunk)
                    digest[1] += len(chunk)
                yield chunk

    async def object_source(self, value):
        for chunk in json_chunks(value):
            yield chunk

    async def result(self, reference, action, *, content=None, result_file=None, output_file=None):
        reference = intent_reference(reference)
        path = f"/adapters/mcp/intents/{reference}/{action}"
        with ExitStack() as stack:
            json_sink = stack.enter_context(self.outputs.output(result_file)) if result_file else io.BytesIO()
            binary_context = self.outputs.output(output_file) if output_file else None
            binary_sink = binary_context.__enter__() if binary_context else None
            binary_cleanup = ExitStack()
            if binary_context:
                failure = invalid('incomplete_download')
                binary_cleanup.callback(binary_context.__exit__, type(failure), failure, None)
                stack.callback(binary_cleanup.close)
            async with self.http.stream('POST', path, content=content) as response:
                if response.headers.get('x-bookflow-mcp-version') != str(BRIDGE_VERSION):
                    raise invalid('incompatible_bridge')
                if response.headers.get('content-type', '').split(';')[0] != 'application/vnd.bookflow.mcp-records':
                    await response.aread()
                    value = self.document(response, reference)
                    # A repeated execute can return an exact cached JSON receipt
                    # or a tagged state. Never interpret a state as business success.
                    if result_file or output_file:
                        raise invalid('file_recovery_unavailable')
                    return value, set(value) == {'code', 'message', 'details'}, {"operation_ref": reference}
                decoder = Decoder(json_sink, binary_sink, operation_ref=reference)
                with anyio.fail_after(self.json_seconds):
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        decoder.feed(chunk)
                terminal = decoder.finish()
                if terminal['binary'] and binary_context:
                    binary_context.__exit__(None, None, None)
                    binary_cleanup.pop_all()
            # No intervening host request: the terminal was the final original-call
            # authority check, including its narrowly scoped self-revocation receipt.
        delivery = {"operation_ref": reference, "recovery": terminal['recovery']}
        if output_file and terminal['binary']:
            delivery['output_file'] = output_file
            delivery['binary'] = terminal['channels']['B']
        if result_file:
            document = {"delivery": "complete_json_file", "operation_ref": reference,
                        "result_file": result_file, **terminal['channels']['J'],
                        "is_error": terminal['is_error'], "recovery": terminal['recovery']}
            if terminal['recovery']['inspection_available']:
                from .inspection import identity
                with self.outputs.input(result_file) as stream:
                    file_identity = identity(stream)
                self.mappings.add(reference, result_file, terminal['channels']['J']['sha256'], file_identity)
        else:
            document = json.loads(json_sink.getvalue())
        return document, terminal['is_error'], delivery

    async def run(self, arguments, *, metadata=None, selection=None):
        reference = None
        try:
            if not isinstance(arguments, RunArguments):
                reference = intent_reference(arguments.operation_ref or arguments.input_ref)
                if arguments.action == 'execute':
                    return await self.result(reference, 'execute', result_file=arguments.result_file,
                                             output_file=arguments.output_file)
                if arguments.action == 'inspect':
                    from .inspection import inspect_file
                    mapping = self.mappings.get(reference)
                    async def authorize():
                        state = await self.post(f'/adapters/mcp/intents/{reference}/status')
                        if state.get('state') != 'completed' or not state.get('inspection_available'):
                            raise invalid('inspection_unavailable')
                    await authorize()
                    inspected = await anyio.to_thread.run_sync(inspect_file, self.outputs, mapping, reference,
                        arguments.pointer, arguments.limit, arguments.cursor)
                    await authorize()
                    return inspected, False, {"operation_ref": reference}
                return await self.post(f'/adapters/mcp/intents/{reference}/{arguments.action}'), False, {"operation_ref": reference}
            files, direction = arguments.transport, metadata['transfer']
            if (files.input_file is not None and direction != 'input') or (files.output_file is not None and direction != 'output'):
                raise BookflowError('E_USAGE', details={"reason": "file_direction"})
            if direction == 'input' and files.input_file is None:
                raise BookflowError('E_USAGE', message='Supply transport.input_file with the permitted business file path.')
            output_file = files.output_file or (self.outputs.destination() if direction == 'output' else None)
            header = arguments.model_dump(exclude_unset=True, exclude={'input', 'transport'})
            admitted = await self.post('/adapters/mcp/intents/new', json={'arguments': header, 'company_selection': selection})
            try:
                reference = intent_reference(admitted.get('operation_ref'))
            except BookflowError:
                raise invalid('invalid_admission') from None
            raw = self.source(files.input_json_file) if files.input_json_file else self.object_source(arguments.input)
            if not direction and not files.prepare_only:
                return await self.result(reference, 'run', content=raw, result_file=files.result_file)
            await self.post(f'/adapters/mcp/intents/{reference}/prepare', content=raw)
            if direction == 'input':
                digest = [hashlib.sha256(), 0]
                observed = await self.post(f'/adapters/mcp/intents/{reference}/input', content=self.source(files.input_file, digest))
                if observed != {'sha256': digest[0].hexdigest(), 'size_bytes': digest[1]}:
                    raise invalid('source_digest')
                await self.post(f'/adapters/mcp/intents/{reference}/seal', json=observed)
            if files.prepare_only:
                return await self.post(f'/adapters/mcp/intents/{reference}/status'), False, {"operation_ref": reference}
            return await self.result(reference, 'execute', result_file=files.result_file, output_file=output_file)
        except BookflowError as exc:
            if exc.code == 'E_IO' and not getattr(exc, 'core_document', False):
                exc.details.setdefault('outcome', 'unknown' if reference else 'not_submitted')
                if reference is not None:
                    exc.details['operation_ref'] = reference
            raise
        except Exception:
            raise BookflowError('E_IO', details={"reason": "transport_failure", "outcome": "unknown", "operation_ref": reference}) from None
