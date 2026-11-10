"""Payroll authority for portable copies of a company containing private records."""
from bookflow.hub import access


def require_archive_read(s):
    """An archive preserves payroll; creating it needs the same private read authority.

    Old companies without payroll tables and empty newly migrated companies keep their
    existing backup admission. Scheduled host backups remain filesystem custody, with
    no archive download endpoint or payroll content in their command responses.
    """
    for table in ('payroll_settings', 'payroll_drafts', 'payroll_draft_revisions', 'payroll_operations', 'payroll_paychecks'):
        if s.company.raw.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            if s.company.raw.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                access.require_resource(s, 'payroll.read', 'member')
                return
