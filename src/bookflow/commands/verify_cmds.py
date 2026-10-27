"""`company verify`: prove the whole of a company's statement effect storage (R180 stage 2).

A reconciliation command proves only what was written after the account's verified watermark,
plus every older closure that touches (`company/reconciliation_watermark.py`). This command proves
everything, as every command did before watermarks: it is what finds an old row changed behind
Bookflow's back. Backups and restores run the same proof. It reads only.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.registry import Plan, command


class VerifyInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    account: str | None = Field(None, description='One bank or card account to verify, by id or full name; every '
                                                  'bank and card account when omitted')


class VerifiedAccount(BaseModel):
    account_id: str
    name: str = Field(description="The account's full name")
    outcome: Literal['proved', 'unsupported', 'pending', 'failed'] = Field(
        description='proved: every stored statement effect equals what the ledger derives and every reconciliation '
                    'record holds together; unsupported: the account holds a document statement reconciliation '
                    'cannot represent, or is in a foreign currency, so nothing is stored to prove; pending: posted '
                    'documents still owe their statement effects, which the next command writes; failed: a check '
                    'refused (see check)')
    check: str | None = Field(None, description='The check that refused, or why it is unsupported; null when proved')
    documents: int = Field(description='Documents posting to the account, each derived again from the ledger')
    effect_versions: int = Field(description='Stored statement effect versions compared')
    certificates: int = Field(description='Finished reconciliations (certificates) checked')
    watermark: str | None = Field(None, description='The account\'s verified watermark: holds, missing, stale (other '
                                                     'code) or broken (the file changed under it); a watermark only '
                                                     'lets reconciliation commands re-prove less, never more')


class DatabaseCheck(BaseModel):
    integrity: str = Field(description="SQLite's integrity check: ok, or the first problem it reports")
    broken_references: int = Field(description='Rows whose declared reference names no row (SQLite foreign key check)')


class VerifyOutput(BaseModel):
    ok: bool = Field(description='True when no account failed and the database checks passed')
    accounts: list[VerifiedAccount]
    database: DatabaseCheck | None = Field(None, description='The whole file\'s SQLite integrity and reference checks; '
                                                             'only when no account is named')
    proves: str = Field(description='What this proof covers, and what a reconciliation command proves on its own')


PROVES = ('Every bank and card account\'s stored statement effects were derived again from the ledger and compared '
          'in full, and every reconciliation, claim, event and receipt checked against the rows it stored. A '
          'reconciliation command proves the same of what was written after the account\'s verified watermark '
          'and of every older record that touches; it does not look again at an older row changed in place '
          'outside Bookflow. That is what this command, and every backup and restore, are for.')


def plan_company_verify(inp: VerifyInput, ctx, s) -> Plan:
    import sqlalchemy as sa
    from bookflow.company import reconciliation_watermark as watermark
    from bookflow.company import schema as c
    from bookflow.company.accounts import resolve_account
    from bookflow.hub import access
    access.require_resource(s, 'ledger.read', 'member')
    db = s.company
    account_id = resolve_account(db, inp.account)['id'] if inp.account else None
    names = dict(db.conn.execute(sa.select(c.accounts.c.id, c.accounts.c.full_name)).all())
    out = [VerifiedAccount(name=names.get(found['account_id'], ''), **found) for found in watermark.verify(db, account_id)]
    database = None
    if account_id is None:
        found = db.raw.execute('PRAGMA integrity_check').fetchone()[0]
        database = DatabaseCheck(integrity=found, broken_references=len(db.raw.execute('PRAGMA foreign_key_check').fetchall()))
    ok = all(v.outcome != 'failed' for v in out) and (database is None or
                                                    (database.integrity == 'ok' and not database.broken_references))
    return Plan(VerifyOutput(ok=ok, accounts=out, database=database, proves=PROVES))


company_verify = command(
    'company verify', scope='company',
    description=('Prove the whole of the company\'s statement reconciliation storage: for every bank and card account '
                 '(or the one named), each stored statement effect is derived again from the ledger and compared, and '
                 'every finished reconciliation, claim and receipt is checked against the rows it stored. '
                 'Reconciliation commands re-prove only what changed since the account was last proved; this finds an '
                 'older record changed outside Bookflow. Backups and restores run it too. Without an account it also '
                 'runs SQLite\'s integrity and reference checks over the whole file. Reads only. Example: '
                 '`bookflow company verify`.'),
    input_model=VerifyInput, output_model=VerifyOutput, required_role='member', capability='ledger.read',
    error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])(plan_company_verify)
