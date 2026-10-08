"""Reading and writing the stored statement lines (co0066) and saved CSV mappings.

What a draft's statement is: the union of every line imported into that draft, each line once.
`gaps` is the check `reconcile preview` and `reconcile finish` run with it.
"""
import json

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company import statement_files as files
from bookflow.core import clock
from bookflow.core.ids import new_id


def draft_lines(conn, draft_id):
    """The draft's statement lines (reconciliation sign) and the widest window they were read with,
    or (None, None) when nothing was imported into the draft."""
    imports = conn.execute(sa.select(c.statement_imports.c.suggest_days)
                           .where(c.statement_imports.c.draft_id == draft_id)).scalars().all()
    if not imports:
        return None, None
    rows = conn.execute(sa.select(c.statement_lines).where(c.statement_lines.c.draft_id == draft_id)
                        .order_by(c.statement_lines.c.date, c.statement_lines.c.import_id,
                                  c.statement_lines.c.ordinal)).mappings().all()
    return [files.Line(line_id=r['line_id'], date=r['date'], amount=r['amount'], payee=r['payee'],
                       memo=r['memo'], number=r['number'], fitid=r['fitid']) for r in rows], max(imports)


def known_line_ids(conn, account_id):
    """Every line id already imported for the account, in any draft: FITID dedupe that persists."""
    return set(conn.execute(sa.select(c.statement_lines.c.line_id)
                            .where(c.statement_lines.c.account_id == account_id)).scalars())


def store(conn, *, identity, account_id, draft_id, parsed_format, file_sha256, statement_date, ending_balance,
          suggest_days, line_count, lines, made):
    """One import row and the lines the draft does not hold yet; returns how many lines were new."""
    held = set(conn.execute(sa.select(c.statement_lines.c.line_id)
                            .where(c.statement_lines.c.draft_id == draft_id)).scalars())
    new = [v for v in lines if v.line_id not in held]
    conn.execute(c.statement_imports.insert().values(
        id=identity, account_id=account_id, draft_id=draft_id, format=parsed_format,
        file_sha256=file_sha256, statement_date=statement_date, ending_balance=ending_balance,
        suggest_days=suggest_days, line_count=line_count, **made))
    if new:
        conn.execute(c.statement_lines.insert(), [dict(
            import_id=identity, ordinal=n, account_id=account_id, draft_id=draft_id,
            line_id=v.line_id, fitid=v.fitid, date=v.date, amount=v.amount, payee=v.payee,
            memo=v.memo, number=v.number) for n, v in enumerate(new)])
    return identity, len(new)


def saved_mapping(conn, account_id, name):
    """The newest mapping saved under `name` for the account, or None."""
    row = conn.execute(sa.select(c.statement_csv_mappings.c.mapping_snapshot)
                       .where(c.statement_csv_mappings.c.account_id == account_id,
                              c.statement_csv_mappings.c.name == name)
                       .order_by(c.statement_csv_mappings.c.created_at.desc(),
                                 c.statement_csv_mappings.c.id.desc()).limit(1)).scalar()
    return None if row is None else json.loads(row)


def save_mapping(conn, *, account_id, name, mapping, created_at, created_by, audit_event_id):
    conn.execute(c.statement_csv_mappings.insert().values(
        id=new_id(), account_id=account_id, name=name,
        mapping_snapshot=json.dumps(mapping, sort_keys=True, separators=(',', ':')),
        created_at=created_at, created_by=created_by, audit_event_id=audit_event_id))


def gaps(s, snapshot, draft):
    """Ticked movements on `draft` that no imported statement line accounts for.

    None when no statement was imported into the draft: such a reconciliation behaves as before.
    """
    from bookflow.commands.reconcile_import_cmds import candidates
    lines, window = draft_lines(s.company.conn, draft.id)
    if lines is None:
        return None
    cutoff = draft.header.statement_date or draft.header.opening_date
    ticked = [v for v in candidates(snapshot, draft.account_id, cutoff, draft) if v.selected]
    return files.cleared_without_line(lines, ticked, suggest_days=window)


def now():
    return clock.now_iso()
