"""Bookflow: a multi-company double-entry accounting core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["connect", "Client", "BookflowError", "Money"]

if TYPE_CHECKING:  # pragma: no cover
    from bookflow.client import Client, connect
    from bookflow.core.errors import BookflowError
    from bookflow.core.money import Money


def __getattr__(name: str) -> Any:
    if name in ("connect", "Client"):
        from bookflow import client
        return getattr(client, name)
    if name == "BookflowError":
        from bookflow.core.errors import BookflowError
        return BookflowError
    if name == "Money":
        from bookflow.core.money import Money
        return Money
    raise AttributeError(name)
