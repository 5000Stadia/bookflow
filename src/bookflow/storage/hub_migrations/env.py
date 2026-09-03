"""Alembic environment for the hub chain; the connection is passed in by migrate.py."""

from alembic import context

from bookflow.hub.schema import metadata

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
