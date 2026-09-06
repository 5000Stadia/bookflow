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
from .responses import annotate, observation


class Client:
    def __init__(self, http, inputs, outputs):
        self.http, self.inputs, self.outputs = http, inputs, outputs
        from .inspection import Mappings
        from .limits import json_seconds
        self.json_seconds = json_seconds()
        self.mappings = Mappings()

    def document(self, response, reference=None, *, body=None, admission=False):
        try:
            if response.headers.get("x-bookflow-mcp-version") != str(BRIDGE_VERSION):
                raise ValueError
            if response.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError
            value = response.json() if body is None else json.loads(body)
            if not isinstance(value, dict):
                raise ValueError
            if response.status_code >= 400:
                if (set(value) != {"code", "message", "details"} or not isinstance(value['details'], dict)
                        or not isinstance(value['code'], str) or not value['code'].startswith('E_')
                        or not isinstance(value['message'], str)):
                    raise ValueError
                error = BookflowError(value['code'], message=value['message'], details=value['details'])
                # Only the freshly guarded pre-intent admission endpoint may
                # return an original command rejection outside verified frames.
                if admission and response.headers.get('x-bookflow-mcp-response') == 'command_rejection':
                    error.command_rejection = True
                raise error
            return value
        except (ValueError, KeyError, TypeError, RecursionError):
            raise invalid("invalid_response") from None

    async def control(self, response, *, admission=False):
        # Control observations/errors have bounded schemas. Complete command
        # documents, including large validation errors, use the framed channel.
        body = bytearray()
        with anyio.fail_after(30):
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > 65536:
                    raise invalid('invalid_control_size')
                body.extend(chunk)
        return self.document(response, body=body, admission=admission)

    async def post(self, path, *, reference=None, kind='state', **kwargs):
        async with self.http.stream('POST', path, **kwargs) as response:
            return observation(await self.control(response, admission=kind == 'admission'), reference, kind=kind)

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

    async def result(self, reference, action, *, content=None, result_file=None, output_file=None, observation_kind="state"):
        reference = intent_reference(reference)
        path = f"/adapters/mcp/intents/{reference}/{action}"
        with ExitStack() as stack:
            def destination(path):
                if not path:
                    return io.BytesIO(), lambda: None
                context = self.outputs.output(path)
                sink = context.__enter__()
                cleanup = ExitStack()
                failure = invalid('incomplete_download')
                cleanup.callback(context.__exit__, type(failure), failure, None)
                stack.callback(cleanup.close)
                def commit():
                    context.__exit__(None, None, None)
                    cleanup.pop_all()
                return sink, commit
            json_sink, commit_json = destination(result_file)
            binary_sink, commit_binary = destination(output_file) if output_file else (None, lambda: None)
            async with self.http.stream('POST', path, content=content) as response:
                if response.headers.get('x-bookflow-mcp-version') != str(BRIDGE_VERSION):
                    raise invalid('incompatible_bridge')
                media_type = response.headers.get('content-type', '').split(';')[0]
                if media_type != 'application/vnd.bookflow.mcp-records':
                    # Only a declared, validated transport observation may be JSON.
                    # A new execution can never complete without framed J/T records.
                    if media_type != 'application/json' or (response.status_code < 400 and action == 'run'):
                        raise invalid('invalid_response')
                    value = observation(await self.control(response), reference, kind=observation_kind)
                    return value, False, {'operation_ref': reference, 'response_kind': 'recovery_observation'}
                if response.status_code != 200:
                    raise invalid('invalid_response')
                decoder = Decoder(json_sink, binary_sink, operation_ref=reference)
                with anyio.fail_after(self.json_seconds):
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        decoder.feed(chunk)
                terminal = decoder.finish()
                if terminal['binary']:
                    commit_binary()
                commit_json()
            # The verified terminal is the final original-call authority check.
        delivery = {'operation_ref': reference, 'recovery': terminal['recovery'], 'response_kind': 'verified_command_completion'}
        if output_file and terminal['binary']:
            delivery['output_file'] = output_file
            delivery['binary'] = terminal['channels']['B']
        if result_file:
            document = {'delivery': 'complete_json_file', 'operation_ref': reference,
                        'result_file': result_file, **terminal['channels']['J'],
                        'is_error': terminal['is_error'], 'recovery': terminal['recovery']}
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
        submitted = False
        try:
            if not isinstance(arguments, RunArguments):
                reference = intent_reference(arguments.operation_ref or arguments.input_ref)
                submitted = True
                if arguments.action == 'execute':
                    return await self.result(reference, 'execute', result_file=arguments.result_file,
                                             output_file=arguments.output_file)
                if arguments.action == 'inspect':
                    from .inspection import inspect_file
                    mapping = self.mappings.get(reference)
                    async def authorize():
                        state = await self.post(f'/adapters/mcp/intents/{reference}/status', reference=reference)
                        if state.get('state') != 'completed' or not state.get('inspection_available'):
                            raise invalid('inspection_unavailable')
                    await authorize()
                    inspected = await anyio.to_thread.run_sync(inspect_file, self.outputs, mapping, reference,
                        arguments.pointer, arguments.limit, arguments.cursor)
                    await authorize()
                    return inspected, False, {"operation_ref": reference, "response_kind": "verified_result_inspection"}
                return await self.post(f'/adapters/mcp/intents/{reference}/{arguments.action}', reference=reference, kind='release' if arguments.action == 'release' else 'state'), False, {"operation_ref": reference, 'response_kind': 'recovery_observation'}
            files, direction = arguments.transport, metadata['transfer']
            if (files.input_file is not None and direction != 'input') or (files.output_file is not None and direction != 'output'):
                raise BookflowError('E_USAGE', details={"reason": "file_direction"})
            if direction == 'input' and files.input_file is None:
                raise BookflowError('E_USAGE', message='Supply transport.input_file with the permitted business file path.')
            output_file = files.output_file or (self.outputs.destination() if direction == 'output' else None)
            header = arguments.model_dump(exclude_unset=True, exclude={'input', 'transport'})
            admitted = await self.post('/adapters/mcp/intents/new', kind='admission', json={'arguments': header, 'company_selection': selection})
            try:
                reference = intent_reference(admitted.get('operation_ref'))
            except BookflowError:
                raise invalid('invalid_admission') from None
            raw = self.source(files.input_json_file) if files.input_json_file else self.object_source(arguments.input)
            if not direction and not files.prepare_only:
                submitted = True
                return await self.result(reference, 'run', content=raw, result_file=files.result_file)
            prepared = await self.result(reference, 'prepare', content=raw, result_file=files.result_file)
            if prepared[2]['response_kind'] == 'verified_command_completion':
                return prepared

            if direction == 'input':
                digest = [hashlib.sha256(), 0]
                received = await self.result(reference, 'input', content=self.source(files.input_file, digest),
                    result_file=files.result_file, observation_kind='digest')
                if received[2]['response_kind'] == 'verified_command_completion':
                    return received
                observed = received[0]
                if observed != {'sha256': digest[0].hexdigest(), 'size_bytes': digest[1]}:
                    raise invalid('source_digest')
                await self.post(f'/adapters/mcp/intents/{reference}/seal', reference=reference, json=observed)
            if files.prepare_only:
                return await self.post(f'/adapters/mcp/intents/{reference}/status', reference=reference), False, {"operation_ref": reference, "response_kind": "recovery_observation"}
            submitted = True
            return await self.result(reference, 'execute', result_file=files.result_file, output_file=output_file)
        except BookflowError as exc:
            if getattr(exc, "command_rejection", False):
                raise
            raise annotate(exc, reference, submitted=submitted)
        except Exception:
            raise annotate(invalid('transport_failure'), reference, submitted=submitted) from None
