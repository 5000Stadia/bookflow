"""Private authenticated visible-history bookmarks; no registered wire cutover."""
import base64
import hashlib
import hmac
import json
from typing import Literal
from bookflow.core.errors import BookflowError
from bookflow.core import publication_audit as publication
from bookflow.hub import audit_projection as projection
from bookflow.hub.history_cursor_keys import read as hub_key
from bookflow.company.ledger_reports import _cursor_key as company_key

DOMAIN = b'bookflow.history.cursor.v2\0'
MAX_LENGTH = 4096


class Bookmark(projection.FrozenView):
    version: Literal[2] = 2
    reader: str
    query: str
    authority: str
    anchor: str | None
    endpoint: str | None
    entry_anchor: str | None
    empty_start: bool


def invalid():
    raise BookflowError('E_VALIDATION', message='Restart the history query without a bookmark.',
                        details={'reason': 'invalid_cursor'})


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def reader_digest(audience):
    i = audience.identity
    return digest([str(i.root), i.actor, i.actor_kind, i.principal, i.epoch, i.hub_admin])


def query_digest(selection):
    return digest(selection.model_dump(mode='json', exclude={
        'anchor', 'endpoint', 'entry_anchor', 'empty_start'}))


def authority_digest(audience, company):
    # Only the selected company, or jointly visible current hub-history scopes.
    # No policy generation, hidden scope, raw membership rows or graph digest.
    scopes = []
    for scope in sorted(audience.comparison.scopes, key=lambda x:(x.kind,x.id)):
        if company is not None and (scope.kind,scope.id) != ('company',company):continue
        if scope.kind not in ('hub','organization','company') or not audience.visible(scope):continue
        signatures = []
        for subject in audience.subjects():
            value = audience.signature(subject,scope)
            signatures.append([subject, value.membership_role, value.hub_admin,
                sorted((r.capability,r.threshold,allowed) for r,allowed in value.company_bits),
                sorted(value.admin_bits)])
        scopes.append([scope.kind,scope.id,signatures])
    return digest(scopes)


def key(reader, selection):
    return hub_key(reader.session.hub) if selection.company is None else company_key(reader.session.company)


def encode64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def decode64(value):
    result = base64.b64decode(value + '='*(-len(value)%4), altchars=b'-_', validate=True)
    if encode64(result) != value:raise ValueError('noncanonical encoding')
    return result


def issue(reader, proof, *, ctx=None):
    publication.revalidate_proof(reader,proof,ctx=ctx)
    if proof.failure is not None or proof.selection.mode == 'show':return None
    audience = projection.make_audience(reader, read_capability='activity' if proof.selection.mode=='activity' else 'audit')
    selection = projection.normalized_selection(audience,proof.selection)
    history = proof.history
    anchor = history.next_anchor
    if selection.mode == 'tail':
        anchor = anchor or selection.anchor
    elif anchor is None:return None
    value = Bookmark(reader=reader_digest(audience), query=query_digest(selection),
        authority=authority_digest(audience,selection.company), anchor=anchor,
        endpoint=None if selection.mode=='tail' else history.endpoint,
        entry_anchor=history.next_entry_anchor,
        empty_start=selection.mode=='tail' and anchor is None)
    raw = canonical(value.model_dump(mode='json'))
    audience.validate()
    return encode64(raw)+'.'+encode64(hmac.digest(key(reader,selection),DOMAIN+raw,'sha256'))


def resume(reader, selection, token, *, ctx=None):
    if type(token) is not str or not 1 <= len(token) <= MAX_LENGTH:invalid()
    if type(selection) is not projection.HistorySelection or selection.mode=='show':invalid()
    if any(getattr(selection,k) is not None for k in ('anchor','endpoint','entry_anchor')) or selection.empty_start:invalid()
    if ctx is not None:publication.open_selected(reader,selection,ctx)
    audience = projection.make_audience(reader, read_capability='activity' if selection.mode=='activity' else 'audit')
    selection = projection.normalized_selection(audience,selection)
    try:
        body,signature = token.split('.')
        raw,signature = decode64(body),decode64(signature)
        if not hmac.compare_digest(signature,hmac.digest(key(reader,selection),DOMAIN+raw,'sha256')):invalid()
        value = Bookmark.model_validate_json(raw)
        if canonical(value.model_dump(mode='json')) != raw:invalid()
    except (ValueError,TypeError,UnicodeError):
        invalid()
    if value.reader != reader_digest(audience) or value.query != query_digest(selection):invalid()
    if value.authority != authority_digest(audience,selection.company):
        raise BookflowError('E_PERMISSION', details={'reason':'authority_changed'})
    try:
        result = projection.HistorySelection.model_validate(dict(selection.model_dump(),
            anchor=value.anchor, endpoint=value.endpoint, entry_anchor=value.entry_anchor, empty_start=value.empty_start))
        # Authenticate visible anchors in this snapshot before returning a recipe.
        if result.mode == 'activity':
            target = projection._activity_target(audience,result)
            for identifier,entry in ((result.anchor,result.entry_anchor),(result.endpoint,None)):
                if identifier is not None:projection._activity_anchor(audience,result,target,identifier,entry)
        else:
            for identifier in (result.anchor,result.endpoint):
                if identifier is not None:projection._visible_anchor(audience,result,identifier)
    except (ValueError,TypeError):
        invalid()
    audience.validate()
    return result
