"""Schema documentation metadata is complete and describes the shipped database structures."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

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
                (bool(index.unique), tuple(column.name for column in index.columns))
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


def _database_shape(inspector: sa.Inspector) -> dict:
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
            "indexes": sorted(
                (bool(index["unique"]), tuple(index["column_names"]))
                for index in inspector.get_indexes(table_name)
            ),
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
        database_shape = _database_shape(sa.inspect(db.engine))
    assert _metadata_shape(metadata) == database_shape
