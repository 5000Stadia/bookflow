"""Owned opaque history batches for the coordinated SSE consumer cutover."""
from dataclasses import dataclass
import json
from bookflow.core.errors import BookflowError
from bookflow.hub.audit_projection import HistorySelection
from bookflow.adapters.http.execution import PublishedDocument, run_history


@dataclass(frozen=True)
class HistoryBatch:
    frames: tuple[str, ...]
    next_cursor: str
    key: str
    more: bool
    document: PublishedDocument


def drain(host, selection, ctx, credential, bookmark=None):
    if type(selection) is not HistorySelection or selection.mode != 'tail':
        raise BookflowError('E_VALIDATION')
    document = run_history(host, selection, ctx, credential, wire=True, bookmark=bookmark)
    frames = []
    last = bookmark
    for item in document['items']:
        last = item['resume_after']
        frames.append(f'id: {last}\nevent: audit\ndata: {json.dumps(item, separators=(",", ":"))}\n\n')
    cursor = document['next_after']
    if type(cursor) is not str or not cursor:
        raise BookflowError('E_IO', details={'stage':'publication','outcome':'unknown'})
    # This frame acknowledges only the scan position. It never claims remote
    # receipt of an audit frame, and comes AFTER all returned event frames.
    if cursor != last:
        frames.append(f'id: {cursor}\nevent: checkpoint\ndata: {{"projection_version":2}}\n\n')
    return HistoryBatch(tuple(frames), cursor, selection.company or 'hub',
                        document['scan_more'], document)
