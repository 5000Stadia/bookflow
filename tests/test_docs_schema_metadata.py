"""Schema documentation metadata is complete and describes the shipped database structures."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex

from bookflow.company import schema as company_schema
from bookflow.hub import schema as hub_schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head


SCOPES = (
    ("hub", hub_schema.metadata),
    ("company", company_schema.metadata),
)
SQLITE_DIALECT = sqlite.dialect()


def _sql_type(type_: sa.types.TypeEngine) -> str:
    return type_.compile(dialect=SQLITE_DIALECT).upper()


def _metadata_shape(metadata: sa.MetaData) -> dict:
    shape = {}
    for table in metadata.tables.values():
        shape[table.name] = {
            "columns": {
                column.name: (_sql_type(column.type), column.nullable, column.primary_key)
                for column in table.columns
            },
            "primary_key": tuple(column.name for column in table.primary_key.columns),
            "unique": sorted(
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, sa.UniqueConstraint)
            ),
            "indexes": sorted(
                str(CreateIndex(index).compile(dialect=SQLITE_DIALECT))
                for index in table.indexes
            ),
            "foreign_keys": sorted(
                (
                    tuple(element.parent.name for element in constraint.elements),
                    constraint.referred_table.name,
                    tuple(element.column.name for element in constraint.elements),
                )
                for constraint in table.foreign_key_constraints
            ),
        }
    return shape


def _database_shape(inspector: sa.Inspector, connection) -> dict:
    shape = {}
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        shape[table_name] = {
            "columns": {
                column["name"]: (
                    _sql_type(column["type"]),
                    column["nullable"],
                    bool(column["primary_key"]),
                )
                for column in inspector.get_columns(table_name)
            },
            "primary_key": tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]),
            "unique": sorted(
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table_name)
            ),
            # SQLAlchemy SQLite reflection omits expression indexes. Compare
            # every physical explicit index's complete DDL instead, including
            # its name, expression, order, uniqueness and partial predicate.
            "indexes": sorted(connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table_name,)).scalars()),
            "foreign_keys": sorted(
                (
                    tuple(constraint["constrained_columns"]),
                    constraint["referred_table"],
                    tuple(constraint["referred_columns"]),
                )
                for constraint in inspector.get_foreign_keys(table_name)
            ),
        }
    return shape


@pytest.mark.parametrize(("scope", "metadata"), SCOPES)
def test_every_table_and_column_has_a_description(scope, metadata):
    missing = []
    for table in metadata.tables.values():
        table_description = table.info.get("description")
        if not isinstance(table_description, str) or not table_description.strip():
            missing.append(table.name)
        for column in table.columns:
            column_description = column.info.get("description")
            if not isinstance(column_description, str) or not column_description.strip():
                missing.append(f"{table.name}.{column.name}")
    assert missing == [], (scope, missing)


@pytest.mark.parametrize(("scope", "metadata"), SCOPES)
def test_documented_metadata_matches_fresh_migrated_database(tmp_path: Path, scope, metadata):
    path = tmp_path / f"{scope}.db"
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, scope, None)
        database_shape = _database_shape(sa.inspect(db.engine), db.conn)
    assert _metadata_shape(metadata) == database_shape


def test_both_deposit_manifests_still_conform_to_the_shipped_models():
    """A schema or model change that the deposit manifests have not been told about.

    `deposit_read_manifest.conform()` pins the exact column set, types, nullability and foreign-key
    edges of every table it names, plus SHA-256 hashes of six models' JSON schemas.
    `deposit_public_manifest.conform()` separately requires an explicit disposition for every field
    of every model it publishes. Either can be broken from a long way away, and has been three
    times: a widened `Literal` on `reconciliation_models.Producer`, an added column on `accounts`,
    and two added fields on `sales_facts.SalesLineProfile`.

    Each time the failure surfaced as HTTP 400 on every deposit page, discovered by an unrelated
    browser test -- and the third also arrived disguised as `E_PERMISSION`, because the pre-proof
    publication fallback presents an internal error as a permission refusal.

    The guards existed; they lived only in deposit test files, which nobody editing `sales_facts`
    or `ledger_schema` has reason to run. This test puts them where a schema change already runs,
    so the break is named at its cause instead of three layers downstream. It costs about two
    seconds.
    """
    from bookflow.company.deposit_public_manifest import conform as public_conform
    from bookflow.company.deposit_read_manifest import conform as private_conform

    private_conform()
    public_conform()
