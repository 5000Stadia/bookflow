"""Private measured query primitives. Not registered or a runtime authority API."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hmac
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bookflow.core.errors import BookflowError

FACTS = b'bookflow.company.query.facts.v2\0'
CURSOR = b'bookflow.company.query.cursor.v2\0'
RECORD = b'bookflow.company.query.record.v2\0'
PARTITION = 200


def canonical(value):
    """Typed, length-framed values, including distinctions JSON would erase."""
    if value is None:
        return b'n'
    if type(value) is bool:
        return b't' if value else b'f'
    if type(value) is int:
        value, tag = str(value).encode('ascii'), b'i'
    elif type(value) is str:
        value, tag = value.encode('utf-8'), b's'
    elif type(value) is bytes:
        tag = b'b'
    elif type(value) in (tuple, list):
        return b'l' + str(len(value)).encode() + b':' + b''.join(canonical(x) for x in value)
    elif type(value) is dict and all(type(k) is str for k in value):
        return b'd' + canonical(tuple((k, value[k]) for k in sorted(value)))
    else:
        raise BookflowError('E_IO', details={'reason': 'invalid_query_fact'})
    return tag + str(len(value)).encode() + b':' + value


def cursor_key(db):
    # Existing DB-owned lifecycle and error behavior; never create on read.
    from bookflow.company.ledger_reports import _cursor_key
    return _cursor_key(db)


def keyed(key, domain, value):
    return hmac.digest(key, domain + canonical(value), 'sha256').hex()


class QueryProof(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    company: str
    owner: Literal['customer'] = 'customer'
    contract: str
    audience: str
    facts: str


class QueryCursor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    v: Literal[2] = 2
    proof: QueryProof
    offset: int = Field(ge=1, le=9223372036854775807)


def invalid_cursor():
    return BookflowError('E_VALIDATION', details={'fields': [
        {'field': 'cursor', 'problem': 'invalid or mismatched continuation; restart without cursor'}]})


def decode_cursor(key, value):
    try:
        if type(value) is not str or not 1 <= len(value) <= 2048:
            raise ValueError()
        body, mac = value.encode('ascii').split(b'.')
        decode = lambda x: base64.b64decode(x + b'=' * (-len(x) % 4), altchars=b'-_', validate=True)
        raw = decode(body)
        if not hmac.compare_digest(decode(mac), hmac.digest(key, CURSOR + raw, 'sha256')):
            raise ValueError()
        return QueryCursor.model_validate_json(raw)
    except (ValueError, UnicodeError, ValidationError):
        raise invalid_cursor() from None


def continuation(key, proof, offset, count, total):
    following = offset + count
    if following >= total:
        return None
    raw = QueryCursor(proof=proof, offset=following).model_dump_json().encode()
    encode = lambda x: base64.urlsafe_b64encode(x).decode().rstrip('=')
    return encode(raw) + '.' + encode(hmac.digest(key, CURSOR + raw, 'sha256'))


def check_proof(previous, current):
    if previous.model_dump(exclude={'facts'}) != current.model_dump(exclude={'facts'}):
        raise invalid_cursor()
    if not hmac.compare_digest(previous.facts, current.facts):
        raise BookflowError('E_QUERY_STALE', details={'restart': 'Repeat the query without cursor.'})


@dataclass(frozen=True, slots=True)
class RoleMask:
    """Current party owner mask, not a granular-policy/full-C claim."""
    partial: bool
    hidden: tuple[str, ...]


def role_mask(session, noun):
    from bookflow.commands.party_cmds import _can_reveal_tax
    if noun not in {'customer', 'vendor', 'employee', 'other-name'}:
        raise ValueError('No party field owner for this recipe')
    partial = noun in {'vendor', 'employee'} and not _can_reveal_tax(session)
    return RoleMask(partial, ('tax_id_last4',) if partial else ())


def project_record(key, audience, noun, row, mask):
    value = dict(row)
    if 'values' in value:
        value['values'] = dict(value['values'])
    for target in (value, value.get('values', {})):
        for field in mask.hidden:
            if field in target:
                target[field] = None
        if mask.partial:
            for field in ('version', 'updated_at', 'updated_by', 'updated_via'):
                if field in target:
                    target[field] = None
    value['projection_revision'] = keyed(key, RECORD, (noun, audience, value))
    return value


def flat_encoder(width):
    """Fixed scalar-row framing; no recursive objects or per-row field names.

    Recipe metadata owns column identities and semantic decoders. Reject unknown
    types rather than silently coercing a broken SQL/source contract.
    """
    prefix = b'l' + str(width).encode('ascii') + b':'
    def encode(values):
        if len(values) != width:
            raise BookflowError('E_IO', details={'reason': 'invalid_query_fact'})
        parts = [prefix]
        for value in values:
            if value is None:
                parts.append(b'n'); continue
            kind = type(value)
            if kind is bool:
                parts.append(b't' if value else b'f'); continue
            if kind is int:
                tag, body = b'i', str(value).encode('ascii')
            elif kind is str:
                tag, body = b's', value.encode('utf-8')
            elif kind is bytes:
                tag, body = b'b', value
            else:
                raise BookflowError('E_IO', details={'reason': 'invalid_query_fact'})
            parts.extend((tag, str(len(body)).encode('ascii'), b':', body))
        return b''.join(parts)
    return encode
