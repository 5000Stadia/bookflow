"""Bounded mappings and incremental navigation of explicitly delivered JSON files."""

import base64
import hashlib
import json
import os
import time
from collections import OrderedDict
from decimal import Decimal

from bookflow.core.errors import BookflowError
from .framing import invalid
from .intents import retained_size, MIB


def identity(stream):
    info = os.fstat(stream.fileno())
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class Mappings:
    def __init__(self, *, clock=time.monotonic):
        self.clock, self.entries = clock, OrderedDict()

    def sweep(self):
        now = self.clock()
        for ref, value in list(self.entries.items()):
            if now - value['created'] >= 300 or now - value['used'] >= 60:
                del self.entries[ref]

    def add(self, reference, path, digest, file_identity):
        self.sweep()
        self.entries[reference] = {'path': path, 'digest': digest, 'identity': file_identity,
                                   'created': self.clock(), 'used': self.clock()}
        while len(self.entries) > 32 or retained_size(self.entries) > 16 * MIB:
            self.entries.popitem(last=False)

    def get(self, reference):
        self.sweep()
        value = self.entries.get(reference)
        if value is None:
            raise invalid('inspection_unavailable')
        value['used'] = self.clock()
        self.entries.move_to_end(reference)
        return value


class HashingReader:
    def __init__(self, stream):
        self.stream, self.digest = stream, hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def select(stream, pointer, offset, limit):
    """Traverse event paths, including literal dots/slashes and array indices."""
    import ijson
    target = tuple(part.replace('~1', '/').replace('~0', '~') for part in pointer.split('/')[1:])
    stack, values = [], []
    found, kind, scalar, total = False, None, None, 0
    builder = None
    capture_depth = None
    capture_key = None
    for event, value in ijson.basic_parse(stream, use_float=False):
        if isinstance(value, Decimal):
            value = float(value)  # Same numeric types as the exact inline JSON decoder.
        if builder is not None:
            builder.event(event, value)
        if event == 'map_key':
            stack[-1]['key'] = value
            continue
        if event in {'end_map', 'end_array'}:
            frame = stack.pop()
            if frame['path'] == target:
                total = frame['count']
            if builder is not None and len(stack) == capture_depth:
                values.append({'key': capture_key, 'value': builder.value} if kind == 'object' else builder.value)
                builder = None
            continue
        parent = stack[-1] if stack else None
        if parent:
            key = parent['key'] if parent['kind'] == 'object' else str(parent['count'])
            path = parent['path'] + (key,)
            index = parent['count']
            parent['count'] += 1
        else:
            path, key, index = (), None, 0
        container = event in {'start_map', 'start_array'}
        if path == target:
            found = True
            kind = ('object' if event == 'start_map' else 'array') if container else 'scalar'
            if not container:
                scalar = value
        if parent and parent['path'] == target and offset <= index < offset + limit:
            if container:
                builder = ijson.ObjectBuilder()
                builder.event(event, value)
                capture_depth, capture_key = len(stack), key
            else:
                values.append({'key': key, 'value': value} if kind == 'object' else value)
        if container:
            stack.append({'path': path, 'kind': 'object' if event == 'start_map' else 'array', 'count': 0, 'key': None})
    if not found or offset > total and kind != 'scalar':
        raise BookflowError('E_VALIDATION', details={'reason': 'inspection_pointer_or_offset'})
    return {'kind': kind, 'value': scalar} if kind == 'scalar' else {'kind': kind, 'items': values, 'total': total}


def inspect_file(caps, mapping, reference, pointer, limit, cursor):
    offset = 0
    if cursor is not None:
        try:
            state = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
            if not isinstance(state, dict) or set(state) != {'digest', 'pointer', 'offset'}:
                raise ValueError
            if type(state['offset']) is not int or state['offset'] < 0:
                raise ValueError
        except (ValueError, UnicodeError, TypeError):
            raise BookflowError('E_VALIDATION', details={'reason': 'malformed_cursor'}) from None
        if state['digest'] != mapping['digest'] or state['pointer'] != pointer:
            raise BookflowError('E_QUERY_STALE', details={'reason': 'inspection_changed'})
        offset = state['offset']
    with caps.input(mapping['path']) as stream:
        if identity(stream) != mapping['identity']:
            raise BookflowError('E_QUERY_STALE', details={'reason': 'delivered_file_changed'})
        reader = HashingReader(stream)
        result = select(reader, pointer, offset, limit)
        if reader.digest.hexdigest() != mapping['digest']:
            raise BookflowError('E_QUERY_STALE', details={'reason': 'delivered_file_changed'})
    next_cursor = None
    if result['kind'] != 'scalar' and offset + len(result['items']) < result['total']:
        next_cursor = base64.urlsafe_b64encode(json.dumps({'digest': mapping['digest'], 'pointer': pointer,
            'offset': offset + len(result['items'])}, separators=(',', ':')).encode()).decode()
    return {'operation_ref': reference, 'pointer': pointer, **result, 'next_cursor': next_cursor}
