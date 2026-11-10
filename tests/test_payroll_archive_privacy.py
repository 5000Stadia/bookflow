"""Portable company copies must not bypass private payroll admission."""
import sqlite3
from types import SimpleNamespace

import pytest

from bookflow.company.payroll_privacy import require_archive_read
from bookflow.core.errors import BookflowError
from bookflow.hub import access


def test_archive_guard_covers_private_rows_and_each_copy_entry_point(monkeypatch):
    from bookflow.commands.backup_cmds import plan_company_backup as company_backup
    from bookflow.company.accountant_review import plan_export, apply_export
    with sqlite3.connect(':memory:') as db:
        session = SimpleNamespace(company=SimpleNamespace(raw=db))
        checked = []
        def refuse(s, capability, threshold):
            checked.append((capability, threshold))
            raise BookflowError('E_PERMISSION')
        monkeypatch.setattr(access, 'require_resource', refuse)
        require_archive_read(session)  # old company, no private tables
        db.execute('CREATE TABLE payroll_operations (output TEXT)')
        require_archive_read(session)  # empty company, unchanged backup authority
        assert checked == []
        db.execute("INSERT INTO payroll_operations VALUES ('private-pay-314159')")
        for entry in (lambda: require_archive_read(session),
                      lambda: company_backup.plan(None, None, session),
                      lambda: company_backup.apply(None, None, session),
                      lambda: plan_export(session, None, None),
                      lambda: apply_export(None, None, session)):
            with pytest.raises(BookflowError) as caught:
                entry()
            assert caught.value.code == 'E_PERMISSION'
            assert '314159' not in str(caught.value.to_dict())
        assert checked == [('payroll.read', 'member')] * 5
        monkeypatch.setattr(access, 'require_resource', lambda s, cap, role: None)
        require_archive_read(session)  # granted reader can retain a complete backup
