"""Registry-generated bounded reads, loaded only for the requested list noun."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from bookflow.company.lists import LIST_DEFINITIONS
from bookflow.company.query import QueryInput, ReferenceItem
from bookflow.core import registry
from bookflow.core.registry import Plan


class _QueryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    projection: Literal["summary", "reference"]
    count: int = Field(ge=0, le=200, description="Number of items returned on this page (not total matches).", json_schema_extra={"sample": 0})
    next_cursor: str | None

    @model_validator(mode="after")
    def matching_projection(self):
        if self.count != len(self.items):
            raise ValueError("count must equal returned items")
        if any((type(item) is ReferenceItem) != (self.projection == "reference") for item in self.items):
            raise ValueError("items must match the declared projection")
        return self


def _register(noun: str) -> None:
    definition = LIST_DEFINITIONS[noun]
    if definition.query_command in registry.REGISTRY:
        return
    show = registry.REGISTRY[f"{noun} show"]
    title = "".join(word.title() for word in noun.split("-"))
    fields = {}
    for name in dict.fromkeys((definition.display_field, *definition.summary_columns)):
        if name in ReferenceItem.model_fields:
            continue
        original = show.output_model.model_fields[name]
        fields[name] = (original.annotation, Field(description=original.description))
    summary = create_model(f"{title}Summary", __base__=ReferenceItem, **fields)
    output = create_model(f"{title}QueryOutput", __base__=_QueryPage, items=(list[summary | ReferenceItem], ...))

    def plan(inp: QueryInput, ctx, session) -> Plan:
        from bookflow.company.query_providers import query_page
        values = query_page(noun, inp, session, principal_id=ctx.on_behalf_of)
        item_model = summary if inp.projection == "summary" else ReferenceItem
        values["items"] = [item_model.model_validate(item) for item in values["items"]]
        return Plan(preview=output.model_validate(values))

    registry.command(
        definition.query_command, scope="company", description=f"Query a bounded page of {definition.plural_label.lower()}.",
        input_model=QueryInput, output_model=output, required_role=show.required_role,
        capability=show.capability, error_codes=list(dict.fromkeys((*registry.REGISTRY[f"{noun} list"].error_codes, "E_QUERY_STALE"))),
    )(plan)


def _load_target(target: str | None = None) -> None:
    for noun in LIST_DEFINITIONS:
        if target is None or target == noun:
            _register(noun)


_load_target(registry.loading_target())
