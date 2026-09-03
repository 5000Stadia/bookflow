"""The context every command receives. Built only by adapters and the Client."""

from __future__ import annotations

import socket
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version as pkg_version

from pydantic import BaseModel, ConfigDict

from bookflow.core.ids import new_id


class Interface(StrEnum):
    cli = "cli"
    http = "http"
    mcp = "mcp"
    gui = "gui"
    python = "python"
    system = "system"


class ActorKind(StrEnum):
    human = "human"
    agent = "agent"
    system = "system"


def client_version() -> str:
    try:
        return pkg_version("bookflow")
    except PackageNotFoundError:  # pragma: no cover
        return "0"


class Context(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_id: str | None = None
    actor_kind: ActorKind | None = None
    on_behalf_of: str | None = None
    interface: Interface
    client_name: str
    client_version: str
    client_host: str
    session_id: str
    request_id: str
    idempotency_key: str | None = None
    reason: str | None = None
    directive_id: str | None = None
    source_ref: str | None = None
    company_id: str | None = None
    hub_admin: bool = False

    @classmethod
    def new(cls, interface: Interface, client_name: str, session_id: str | None = None, **kw) -> "Context":
        return cls(
            interface=interface,
            client_name=client_name,
            client_version=client_version(),
            client_host=socket.gethostname(),
            session_id=session_id or new_id(),
            request_id=new_id(),
            **kw,
        )


CONTEXT_FIELD_NAMES = frozenset(Context.model_fields)
