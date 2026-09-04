"""Alembic environment for the company chain; the connection is passed in by migrate.py."""

from alembic import context

from bookflow.company.schema import metadata

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=metadata, render_as_batch=True, transaction_per_migration=False)
context.run_migrations()
