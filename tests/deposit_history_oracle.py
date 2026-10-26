"""Test-only oracle: the deposit dependency reader as it was before R180 increment 2.

`FullScanHistory` reads every owner kind whole (every row and every audit entry of the
kind) and resolves every relation by scanning every identity, exactly as
`deposit_dependency_history.History` did before its reads were bounded. The bounded
reader must give the same read set (records, relations, digest, unknowns) and the same
first refusal; tests/test_deposit_history_bounded.py checks that. Keep it for one
increment, as tests/permission_policy_oracle.py is kept.
"""
from contextlib import contextmanager

import sqlalchemy as sa

from bookflow.company import deposit_dependency_history as owner
from bookflow.company import schema as c
from bookflow.company.deposit_dependency_history import (History, MissingHistory, OWNERS, SOURCE_OWNERS,
                                                         _audit_image, _source_owner_image, canonical)
import json


class FullScanHistory(History):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commercial_images = {}   # each reader decoded its own, as before
        self.chains = {}

    def chain(self, kind, identity):
        self.load(kind)
        return self._chain(kind, identity)

    def load(self, kind):
        if kind in self.raw:
            return
        if kind == 'source_price_version':
            return History.load(self, kind)
        owner_ = OWNERS[kind]
        table = c.metadata.tables[owner_.table]
        self.raw[kind] = {row[owner_.key]: dict(row) for row in self.s.company.conn.execute(sa.select(table)).mappings()}
        if kind in SOURCE_OWNERS:
            self.raw[kind] = {key: json.loads(canonical(_source_owner_image(self.s, kind, value))) for key, value in self.raw[kind].items()}
        self.entries[kind] = {}
        query = sa.select(c.audit_entries, c.audit_events.c.seq, c.audit_events.c.at,
                          c.audit_events.c.actor_id, c.audit_events.c.actor_kind,
                          c.audit_events.c.on_behalf_of, c.audit_events.c.interface).join(
            c.audit_events, c.audit_events.c.id == c.audit_entries.c.event_id).where(
            c.audit_entries.c.record_type.in_(owner_.audit_kinds)).order_by(c.audit_events.c.seq, c.audit_entries.c.id)
        for row in self.s.company.conn.execute(query).mappings():
            item = dict(row)
            self.entries[kind].setdefault(item['record_id'], []).append(item)
            self.events[item['event_id']] = item

    def has(self, kind, identity):
        self.load(kind)
        return identity in self.identities(kind)

    def prefetch(self, kind, identities):
        pass

    def prefetch_owners(self, kind, field, owners):
        pass

    def find_owners(self, kind, field, owners):
        index_key = (kind, field, self.cutoff, self.historical)
        if index_key not in self.relation_indexes:
            self.load(kind)
            indexed = {}
            for identity in self.identities(kind):
                candidate = self.raw[kind].get(identity)
                if self.historical:
                    entries = [entry for entry in self.entries[kind].get(identity, ())
                               if entry['seq'] <= self.cutoff and not (kind in ('company_info', 'source_company') and entry['action'] == 'migrate')]
                    if not entries:
                        continue
                    entry = entries[-1]; image_key = (kind, identity, entry['id'])
                    if image_key not in self.find_images:
                        self.find_images[image_key] = _audit_image(entry['after'])
                    candidate = self.find_images[image_key]
                elif candidate is None:
                    entries = self.entries[kind].get(identity, ())
                    if entries:
                        candidate = _audit_image(entries[-1]['after'])
                if candidate is None:
                    continue
                if field not in candidate:
                    raise MissingHistory('incomplete historical relation image')
                indexed.setdefault(candidate[field], []).append(identity)
            self.relation_indexes[index_key] = indexed
        indexed = self.relation_indexes[index_key]
        identities = sorted({identity for owner_ in owners for identity in indexed.get(owner_, ())})
        result = []
        for identity in identities:
            value = self.take(kind, identity)
            if value is not None:
                result.append(value)
        return result

    def find(self, kind, predicate, where=None):
        result = []
        self.load(kind)
        for identity in self.identities(kind):
            candidate = self.raw[kind].get(identity)
            if self.historical:
                entries = [entry for entry in self.entries[kind].get(identity, ())
                           if entry['seq'] <= self.cutoff and not (kind in ('company_info', 'source_company') and entry['action'] == 'migrate')]
                if not entries:
                    continue
                entry = entries[-1]
                image_key = (kind, identity, entry['id'])
                if image_key not in self.find_images:
                    self.find_images[image_key] = _audit_image(entry['after'])
                candidate = self.find_images[image_key]
            if candidate is None and not self.historical:
                entries = self.entries[kind].get(identity, ())
                if entries:
                    candidate = _audit_image(entries[-1]['after'])
            if candidate is None:
                continue
            if kind in ('custom_field', 'source_custom_field') and not self.historical:
                from bookflow.commands.custom_field_cmds import _snapshot
                candidate = _snapshot(self.s.company.conn, identity)
            try:
                selected = predicate(candidate)
            except (KeyError, TypeError) as exc:
                raise MissingHistory('incomplete historical relation image') from exc
            if selected:
                value = self.take(kind, identity)
                if value is not None:
                    result.append(value)
        return result


@contextmanager
def full_scan():
    """Run the deposit reader as before: whole-kind reads, and no per-preparation memo."""
    saved = owner.History, getattr(owner, '_preparation', None)
    owner.History = FullScanHistory
    if saved[1] is not None:
        owner._preparation = lambda s: None
    try:
        yield
    finally:
        owner.History = saved[0]
        if saved[1] is not None:
            owner._preparation = saved[1]
