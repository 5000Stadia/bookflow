"""Register one noun's six Row 5 lifecycle commands.

The factory receives domain-owned Pydantic models and callbacks.  It imports
no noun module, preserving the registry's lazy command loading boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from bookflow.company.lists import get_list_definition
from bookflow.core.context import Context
from bookflow.core.registry import Applied, Command, Plan, REGISTRY, command
from bookflow.core.session import Session


PlanCallback = Callable[[BaseModel, Context, Session], Plan]
ApplyCallback = Callable[[Plan, Context, Session], Applied]
LIFECYCLE_VERBS = ("create", "update", "show", "list", "activate", "deactivate")


@dataclass(frozen=True)
class ModelPair:
    input: type[BaseModel]
    output: type[BaseModel]


@dataclass(frozen=True)
class LifecycleModels:
    create: ModelPair
    update: ModelPair
    show: ModelPair
    list: ModelPair
    activate: ModelPair
    deactivate: ModelPair


@dataclass(frozen=True)
class LifecycleCallbacks:
    create_plan: PlanCallback
    create_apply: ApplyCallback
    update_plan: PlanCallback
    update_apply: ApplyCallback
    show_plan: PlanCallback
    list_plan: PlanCallback
    activate_plan: PlanCallback
    activate_apply: ApplyCallback
    deactivate_plan: PlanCallback
    deactivate_apply: ApplyCallback

    def plan_for(self, verb: str) -> PlanCallback:
        return getattr(self, f"{verb}_plan")

    def apply_for(self, verb: str) -> ApplyCallback | None:
        return None if verb in ("show", "list") else getattr(self, f"{verb}_apply")


@dataclass(frozen=True)
class LifecycleCommands:
    create: Command
    update: Command
    show: Command
    list: Command
    activate: Command
    deactivate: Command

    def as_tuple(self) -> tuple[Command, ...]:
        return tuple(getattr(self, verb) for verb in LIFECYCLE_VERBS)


_SHARED_ERRORS: dict[str, tuple[str, ...]] = {
    "create": ("E_NAME_TAKEN", "E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE", "E_HIERARCHY_DEPTH"),
    "update": ("E_RECORD_NOT_FOUND", "E_NAME_TAKEN", "E_VERSION_CONFLICT", "E_INACTIVE_REFERENCE", "E_HIERARCHY_CYCLE", "E_HIERARCHY_DEPTH"),
    "show": ("E_RECORD_NOT_FOUND",),
    "list": ("E_LIST_FILTER",),
    "activate": ("E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_INACTIVE_REFERENCE", "E_NAME_TAKEN"),
    "deactivate": ("E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_ACTIVE_DEPENDENTS", "E_RECORD_IN_USE", "E_SYSTEM_RECORD"),
}


def _validate_models(models: LifecycleModels, selector_field: str) -> None:
    for verb in LIFECYCLE_VERBS:
        pair = getattr(models, verb)
        if not isinstance(pair, ModelPair):
            raise TypeError(f"{verb}: an explicit ModelPair is required")
        for label, model in (("input", pair.input), ("output", pair.output)):
            if not isinstance(model, type) or not issubclass(model, BaseModel):
                raise TypeError(f"{verb}: {label} must be a Pydantic model class")
    for verb in ("update", "show", "activate", "deactivate"):
        if selector_field not in getattr(models, verb).input.model_fields:
            raise ValueError(f"{verb}: input model omits selector field {selector_field!r}")


def _validate_callbacks(callbacks: LifecycleCallbacks) -> None:
    for verb in LIFECYCLE_VERBS:
        if not callable(callbacks.plan_for(verb)):
            raise TypeError(f"{verb}: plan callback is required")
        apply = callbacks.apply_for(verb)
        if verb not in ("show", "list") and not callable(apply):
            raise TypeError(f"{verb}: apply callback is required")


def register_lifecycle(
    noun: str,
    *,
    models: LifecycleModels,
    callbacks: LifecycleCallbacks,
    selector_field: str,
    error_codes: dict[str, tuple[str, ...] | list[str]] | None = None,
    capability: str | None = None,
    required_read_role: str = "member",
    required_write_role: str = "standard",
    descriptions: dict[str, str] | None = None,
) -> LifecycleCommands:
    """Register exactly create/update/show/list/activate/deactivate for ``noun``.

    Idempotency, dry-run, transactions, reason/directive checks, expected-
    version routing, and audit writing remain properties of the existing
    dispatcher and the domain callbacks.
    """
    definition = get_list_definition(noun)
    if definition is None:
        raise ValueError(f"{noun!r} is not a declared Row 5 list noun")
    _validate_models(models, selector_field)
    _validate_callbacks(callbacks)
    unknown_description_verbs = set(descriptions or ()) - set(LIFECYCLE_VERBS)
    unknown_error_verbs = set(error_codes or ()) - set(LIFECYCLE_VERBS)
    if unknown_description_verbs or unknown_error_verbs:
        raise ValueError(
            "unknown lifecycle verb metadata: "
            f"{sorted(unknown_description_verbs | unknown_error_verbs)}"
        )
    names = {verb: f"{noun} {verb}" for verb in LIFECYCLE_VERBS}
    occupied = sorted(name for name in names.values() if name in REGISTRY)
    if occupied:
        raise ValueError(f"commands already registered: {occupied}")

    label = definition.singular_label.lower()
    plural = definition.plural_label.lower()
    default_descriptions = {
        "create": f"Create a {label}.",
        "update": f"Update a {label}.",
        "show": f"Show one {label}.",
        "list": f"List {plural}.",
        "activate": f"Activate a {label}.",
        "deactivate": f"Deactivate a {label}.",
    }
    if descriptions:
        default_descriptions.update(descriptions)
    for verb, description in default_descriptions.items():
        if not description.endswith("."):
            raise ValueError(f"{verb}: description must end with a period")

    registered: dict[str, Command] = {}
    extra_errors = error_codes or {}
    try:
        for verb in LIFECYCLE_VERBS:
            pair = getattr(models, verb)
            writes = set() if verb in ("show", "list") else {"company"}
            role = required_read_role if not writes else required_write_role
            positional = [selector_field] if verb in ("update", "show", "activate", "deactivate") else []
            version_source = (
                (names["show"], selector_field, "version")
                if verb in ("update", "activate", "deactivate")
                else None
            )
            codes = list(dict.fromkeys((*_SHARED_ERRORS[verb], *extra_errors.get(verb, ()))))
            registrar = command(
                names[verb],
                scope="company",
                description=default_descriptions[verb],
                input_model=pair.input,
                output_model=pair.output,
                writes=writes,
                required_role=role,
                positional=positional,
                error_codes=codes,
                accepts_idempotency_key=verb == "create",
                clearable=verb == "update",
                capability=capability or noun,
                version_source=version_source,
            )
            cmd = registrar(callbacks.plan_for(verb))
            apply = callbacks.apply_for(verb)
            if apply is not None:
                cmd.applier(apply)  # type: ignore[attr-defined]
            registered[verb] = cmd
    except BaseException:
        for name in names.values():
            REGISTRY.pop(name, None)
        raise
    return LifecycleCommands(**registered)  # type: ignore[arg-type]


__all__ = [
    "ApplyCallback",
    "LIFECYCLE_VERBS",
    "LifecycleCallbacks",
    "LifecycleCommands",
    "LifecycleModels",
    "ModelPair",
    "PlanCallback",
    "register_lifecycle",
]
