"""The command registry. Every adapter is generated from it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from bookflow.core.context import CONTEXT_FIELD_NAMES
from bookflow.core.errors import ALL_CODES

Role = str  # "member", "admin", "owner", "hub_admin", or None for any actor


@dataclass
class Touched:
    record_type: str
    record_id: str
    action: str  # create, update, delete, deactivate, migrate, baseline
    version_before: int | None
    version_after: int | None
    after: dict[str, Any] | None
    before: dict[str, Any] | None = None
    db: str = "hub"  # which database's event this entry belongs to: "hub" or "company"


@dataclass
class Plan:
    """What `apply` will do. ``preview`` is the output a real run would return."""

    preview: BaseModel
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Applied:
    output: BaseModel
    touched: list[Touched]
    summary: str
    audited: bool = False  # True when apply wrote its own audit events


@dataclass
class Command:
    name: str
    scope: str  # "hub" or "company"
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    plan: Callable[..., Plan]
    apply: Callable[..., Applied] | None
    writes: frozenset[str] = frozenset()  # subset of {"hub", "company", "config"}
    required_role: Role | None = None
    positional: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    bootstrap: bool = False
    kind: str = "read"  # read, write, advisory; derived from writes unless advisory is declared
    truth: str = "hub"  # the database whose transaction commits first and holds the idempotency row
    accepts_idempotency_key: bool = False
    clearable: bool = False  # update commands whose fields may be set to null (--clear)
    streams: bool = False  # commands with a CLI-only --follow form
    ledger: bool = False
    capability: str = ""  # dotted area a membership may be granted or denied (blueprint 4.3b); derived from the noun
    feature: str | None = None  # a per-company unlockable service this command needs (blueprint 4.3b)

    @property
    def is_write(self) -> bool:
        return self.kind == "write"

    @property
    def noun(self) -> str:
        """Everything but the last word: `company`, or `hub audit`."""
        parts = self.name.split(" ")
        return " ".join(parts[:-1]) if len(parts) > 1 else parts[0]

    @property
    def verb(self) -> str:
        parts = self.name.split(" ")
        return parts[-1] if len(parts) > 1 else ""


REGISTRY: dict[str, Command] = {}


def command(name: str, *, scope: str, description: str, input_model: type[BaseModel], output_model: type[BaseModel],
            writes: set[str] | frozenset[str] = frozenset(), required_role: Role | None = None,
            positional: list[str] | None = None, error_codes: list[str] | None = None, bootstrap: bool = False,
            kind: str | None = None, truth: str | None = None, accepts_idempotency_key: bool = False,
            clearable: bool = False, streams: bool = False, capability: str | None = None, feature: str | None = None):
    """Register ``plan`` (and, via ``.apply``, the apply function) under ``name``."""
    bad = set(input_model.model_fields) & CONTEXT_FIELD_NAMES
    if bad:
        raise ValueError(f"{name}: input model declares context field(s) {sorted(bad)}")
    for code in error_codes or []:
        if code not in ALL_CODES:
            raise ValueError(f"{name}: unknown error code {code}")
    if scope not in ("hub", "company"):
        raise ValueError(f"{name}: bad scope {scope}")
    derived_kind = "write" if writes else "read"
    if kind is None:
        kind = derived_kind
    elif kind == "advisory":
        if "company" not in writes:
            raise ValueError(f"{name}: advisory commands must open the company writable")
    elif kind != derived_kind:
        raise ValueError(f"{name}: declared kind {kind!r} disagrees with writes {sorted(writes)}")
    if truth is None:
        truth = "company" if scope == "company" and "company" in writes else "hub"
    if truth not in ("hub", "company"):
        raise ValueError(f"{name}: bad truth {truth}")

    def register(plan_fn: Callable[..., Plan]) -> Command:
        cmd = Command(name=name, scope=scope, description=description, input_model=input_model, output_model=output_model,
                      plan=plan_fn, apply=None, writes=frozenset(writes), required_role=required_role,
                      positional=list(positional or []), error_codes=list(error_codes or []), bootstrap=bootstrap,
                      kind=kind, truth=truth, accepts_idempotency_key=accepts_idempotency_key, clearable=clearable, streams=streams,
                      capability=capability or name.split(" ")[0], feature=feature)
        REGISTRY[name] = cmd

        def applier(apply_fn: Callable[..., Applied]) -> Callable[..., Applied]:
            cmd.apply = apply_fn
            return apply_fn

        cmd.applier = applier  # type: ignore[attr-defined]
        register.applier = applier  # type: ignore[attr-defined]
        register.cmd = cmd  # type: ignore[attr-defined]
        return cmd

    return register


def get(name: str) -> Command | None:
    return REGISTRY.get(name)


def all_commands() -> list[Command]:
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def load_all() -> None:
    """Import every command module so the registry is complete."""
    import bookflow.commands.hub_cmds  # noqa: F401
    import bookflow.commands.company_cmds  # noqa: F401
    import bookflow.commands.audit_cmds  # noqa: F401
