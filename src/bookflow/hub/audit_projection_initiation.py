"""Finite direct initiators in already authorized, disclosed company entries.

This is a rendering decision, not an authority or raw-snapshot decoder. Call only
after owner validation and disclosure; derived records never substitute for the
typed initiating header/receipt. Existing initiation rules remain in the caller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import audit_projection_legacy as legacy
from . import audit_projection_deposit_drafts as drafts
from .audit_projection_deposit_coordinate import CoordinateOperation

if TYPE_CHECKING:
    from .audit_projection import ProjectedEntry


DIRECT = {
    'deposit_draft': (drafts.Draft, frozenset((
        'deposit draft create', 'deposit draft update', 'deposit draft clear', 'deposit draft abandon'))),
    'deposit_selection': (drafts.Selection, frozenset((
        'deposit selection create', 'deposit selection update', 'deposit selection clear',
        'deposit selection abandon', 'deposit selection accept', 'deposit selection select-matching'))),
    # Upload currently writes chunks/items, not this header, so it stays generic.
    # A future header-writing upload needs an explicit rendering-contract review.
    'payment_selection_recovery': (legacy.RecoveryHeaderView, frozenset((
        'payment recovery begin', 'payment recovery upload', 'payment recovery seal',
        'payment recovery apply', 'payment recovery abort', 'payment recovery replace'))),
    'note': (legacy.NoteView, frozenset(('note add', 'note edit'))),
    'directive': (legacy.DirectiveView, frozenset(('directive add', 'directive deactivate'))),
    'exchange_rate': (legacy.ExchangeRateView, frozenset(('rate set',))),
    'attachment_link': (legacy.AttachmentLinkView, frozenset((
        'attachment add', 'attachment link', 'attachment unlink'))),
    'attachment_collection': (legacy.AttachmentCollectionView, frozenset(('company compact',))),
}
RECEIPTS = {
    legacy.PaymentOperationView: frozenset(('payment receive', 'payment apply', 'payment unapply',
                                          'payment update', 'payment void')),
    legacy.InvoiceOperationView: frozenset(('invoice update',)),
    legacy.DepositOperationView: frozenset(('deposit post', 'deposit update', 'deposit void')),
    CoordinateOperation: frozenset(('deposit coordinate',)),
}


def direct_command(command: str, entries: tuple[ProjectedEntry, ...]) -> str | None:
    """Recognize only a surviving direct initiator, never arbitrary command text."""
    for entry in entries:
        for image in (entry.after, entry.before):
            if image is None:
                continue
            if entry.identity.kind == 'transaction' and type(image) is legacy.TransactionView:
                if command in ('register post', 'register update') and image.type == 'journal_entry':
                    return command
            rule = DIRECT.get(entry.identity.kind)
            if rule is not None and type(image) is rule[0] and command in rule[1]:
                return command
            if entry.identity.kind in ('payment_operation', 'deposit_operation'):
                if command in RECEIPTS.get(type(image), ()) and image.command == command:
                    return command
    return None
