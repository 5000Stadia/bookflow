"""Bookflow: a multi-company double-entry accounting core."""

from bookflow.client import Client, connect
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money

__all__ = ["connect", "Client", "BookflowError", "Money"]
