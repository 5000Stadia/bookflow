"""Exact retained header proofs, after the draft owner's complete admission.

One decoder-local reader; no cross-request cache, current-state substitution or
permission supplier. Numeric versions use raw entries, not semantic anchors.
"""
from dataclasses import dataclass

import sqlalchemy as sa
from pydantic import ValidationError

from bookflow.company import schema as c, deposit_dependency_history as history
from bookflow.company.deposit_models import Effect
from bookflow.core.errors import BookflowError


def require(ok):
    if not ok:
        raise BookflowError('E_VALIDATION', details={'reason': 'deposit_draft_history'})


@dataclass(frozen=True)
class HeaderEndpoint:
    header: dict
    sequence: int
    event_id: str


class DraftHistoryProof:
    def __init__(self, s, header, revision, kind):
        self.s = s
        self.reader = history.History(s)
        self.copy_sources = {}
        self.copy_sequence = None
        require(kind in ('draft', 'selection'))
        if kind == 'selection':
            require((revision['target_draft_id'], revision['target_revision_id']) ==
                    (header['target_draft_id'], header['target_revision_id']))
            root = s.company.conn.execute(sa.select(c.deposit_drafts).where(
                c.deposit_drafts.c.id == header['target_draft_id'])).mappings().one_or_none()
            require(root is not None)
            target = s.company.conn.execute(sa.select(c.deposit_draft_revisions.c.id).where(
                c.deposit_draft_revisions.c.id == header['target_revision_id'],
                c.deposit_draft_revisions.c.draft_id == root['id'])).scalar_one_or_none()
            require(target is not None)
        else:
            root = header
        first = s.company.conn.execute(sa.select(c.deposit_draft_revisions).where(
            c.deposit_draft_revisions.c.draft_id == root['id'],
            c.deposit_draft_revisions.c.version == 1)).mappings().one_or_none()
        require(first is not None)
        creation = self.event_sequence(first['audit_event_id'])
        require(creation <= self.event_sequence(revision['audit_event_id']))
        edit, copied = root['edit_transaction_id'], root['copy_transaction_id']
        require(not (edit and copied))
        if edit or copied:
            identity = edit or copied
            version = root['baseline_version'] if edit else root['copy_version']
            revision_id = root['baseline_revision_id'] if edit else root['copy_revision_id']
            endpoint = self.exact_header(identity, version)
            require(endpoint.header['type'] == 'deposit' and
                    endpoint.header['current_revision_id'] == revision_id and
                    endpoint.header['status'] == ('posted' if edit else 'voided'))
            require(self.at_event(identity, creation).header['version'] == version)
            profile = s.company.conn.execute(sa.select(c.deposit_profiles).where(
                c.deposit_profiles.c.transaction_id == identity,
                c.deposit_profiles.c.revision_id == revision_id)).mappings().one_or_none()
            require(profile is not None)
            if copied:
                try:
                    effect = Effect.model_validate_json(profile['facts_snapshot'])
                except ValidationError:
                    require(False)
                self.copy_sources = {r.source.transaction_id: r.source for r in effect.intent.sources}
                require(len(self.copy_sources) == len(effect.intent.sources))
                self.copy_sequence = creation

    def event_sequence(self, event_id):
        sequence = self.s.company.conn.execute(sa.select(c.audit_events.c.seq).where(
            c.audit_events.c.id == event_id)).scalar_one_or_none()
        require(sequence is not None)
        return sequence

    def exact_header(self, identity, version):
        require(type(version) is int and version > 0)
        self.reader.load('transaction')
        entries = self.reader.entries['transaction'].get(identity, ())
        selected = [r for r in entries if r['version_after'] == version]
        require(len(selected) == 1)
        endpoint = selected[0]
        cutoff = self.reader.cutoff
        try:
            self.reader.cutoff = endpoint['seq']
            self.reader.chain('transaction', identity)  # Validate every predecessor, not its coalesced result.
            for entry in entries:
                if entry['seq'] > endpoint['seq']:
                    break
                before = history._audit_image(entry['before'])
                if before is not None:
                    require(type(before.get('version')) is int and before['version'] == entry['version_before'])
                require(type(entry['version_after']) is int and entry['version_after'] > 0)
                if entry['action'] != 'baseline':
                    require(entry['version_after'] == (1 if entry['version_before'] is None else entry['version_before'] + 1))
            raw = history._audit_image(endpoint['after'])
            require(raw is not None and raw.get('id') == identity and
                    type(raw.get('version')) is int and raw['version'] == version)
            current = self.reader.raw['transaction'].get(identity)
            require(current is not None and raw['type'] == current['type'])
            # This validates real revision ownership, schema and creation <= event.
            self.reader.commercial(raw, endpoint['seq'])
        except history.MissingHistory:
            require(False)
        finally:
            self.reader.cutoff = cutoff
        return HeaderEndpoint(raw, endpoint['seq'], endpoint['event_id'])

    def at_event(self, identity, sequence):
        self.reader.load('transaction')
        entries = [r for r in self.reader.entries['transaction'].get(identity, ()) if r['seq'] <= sequence]
        require(bool(entries))
        latest = max(entries, key=lambda r: r['seq'])
        require(sum(r['seq'] == latest['seq'] for r in entries) == 1)
        return self.exact_header(identity, latest['version_after'])

    def source_endpoint(self, row):
        source = row.source
        copied = row.captured_header_version is not None
        version = row.captured_header_version if copied else source.expected_header_version
        endpoint = self.exact_header(source.transaction_id, version)
        require(endpoint.header['type'] == source.source_type and
                endpoint.header['current_revision_id'] == source.revision_id and
                endpoint.header['status'] == 'posted')
        if copied:
            original = self.copy_sources.get(source.transaction_id)
            require(original is not None and self.copy_sequence is not None)
            require(original.expected_header_version == version)
            require(source.model_copy(update={'expected_header_version': version}) == original)
            observed = self.at_event(source.transaction_id, self.copy_sequence)
            require(observed.header['version'] == source.expected_header_version)
            require(endpoint.sequence <= self.copy_sequence)
        return endpoint
