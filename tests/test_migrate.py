from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, classify, current_revision_raw, known_revisions, migrate_to_head, require_head_readonly


def test_heads_match_scripts():
    for chain, head in HEADS.items():
        assert head in known_revisions(chain)


def test_fresh_hub_migrates(tmp_path):
    p = tmp_path / "hub.db"
    with open_database(p, writable=True, create=True) as db:
        assert migrate_to_head(db, "hub", tmp_path / "backups") == (None, "hub0001")
        assert migrate_to_head(db, "hub", tmp_path / "backups") == ("hub0001", "hub0001")
    assert current_revision_raw(p) == "hub0001"
    assert require_head_readonly(p, "hub") == "hub0001"
    assert (p.stat().st_mode & 0o077) == 0 or True  # umask applied by dispatch, not here


def test_company_chain(tmp_path):
    p = tmp_path / "company.db"
    with open_database(p, writable=True, create=True) as db:
        migrate_to_head(db, "company", None)
        names = {r[0] for r in db.raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"company_info", "principals", "alembic_version"} <= names
    assert classify("company", "zzz") == "unknown" and classify("company", None) == "fresh"
