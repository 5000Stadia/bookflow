"""Registry-generated bounded reads, loaded only for the requested list noun."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator, model_serializer

from bookflow.company.lists import LIST_DEFINITIONS
from bookflow.company.query import QueryInput, ReferenceItem
from bookflow.company.query_models import Descriptor, SelectedItem, OptionsInput, OptionsOutput
from bookflow.core import registry
from bookflow.core.registry import Plan


class _QueryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    projection: Literal["summary", "reference"]
    count: int = Field(ge=0, le=200, description="Number of items returned on this page (not total matches).", json_schema_extra={"sample": 0})
    next_cursor: str | None
    columns: list[Descriptor] | None = None
    matching_total: int | None = Field(default=None, ge=0, description='Total matching records, supplied only for explicitly selected columns.')

    @model_serializer(mode='wrap')
    def compatible(self, handler):
        result = handler(self)
        if self.columns is None:
            result.pop('columns', None)
            result.pop('matching_total', None)
        return result

    @model_validator(mode="after")
    def matching_projection(self):
        if self.count != len(self.items):
            raise ValueError("count must equal returned items")
        if self.columns is not None:
            if self.projection != 'summary' or self.matching_total is None or self.matching_total < self.count:
                raise ValueError('Selected columns require summary and an exact matching total')
            keys = {column.key for column in self.columns}
            if any(not isinstance(item, SelectedItem) or set(item.values) != keys for item in self.items):
                raise ValueError('Selected row keys must match column descriptors')
            return self
        if self.matching_total is not None or any(isinstance(item, SelectedItem) for item in self.items):
            raise ValueError('Legacy projections cannot contain selected values or totals')
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
    output = create_model(f"{title}QueryOutput", __base__=_QueryPage, items=(list[summary | ReferenceItem | SelectedItem], ...))

    def plan(inp: QueryInput, ctx, session) -> Plan:
        from bookflow.company.query_providers import query_page
        values = query_page(noun, inp, session, principal_id=ctx.on_behalf_of)
        item_model = SelectedItem if inp.columns is not None else summary if inp.projection == "summary" else ReferenceItem
        values["items"] = [item_model.model_validate(item) for item in values["items"]]
        return Plan(preview=output.model_validate(values))

    registry.command(
        definition.query_command, scope="company", description=f"Query a bounded page of {definition.plural_label.lower()}.",
        input_model=QueryInput, output_model=output, required_role=show.required_role,
        capability=show.capability, error_codes=list(dict.fromkeys((*registry.REGISTRY[f"{noun} list"].error_codes, "E_QUERY_STALE"))),
    )(plan)

    def discover(inp: OptionsInput, ctx, session) -> Plan:
        from bookflow.company.query_catalog import options
        return Plan(preview=options(noun, inp, session, principal_id=ctx.on_behalf_of))

    registry.command(f'{noun} query options', scope='company',
        description=f'Discover a bounded page of named columns, filters, sorts or custom choices for {definition.plural_label.lower()}.',
        input_model=OptionsInput, output_model=OptionsOutput, required_role=show.required_role,
        capability=show.capability, error_codes=['E_LIST_FILTER', 'E_QUERY_STALE'])(discover)

    from bookflow.company.query_projection import COLLECTIONS
    if any(owner == noun for owner, _column in COLLECTIONS):
        from bookflow.company.query_children import ChildrenInput, ChildrenOutput, children

        def child_page(inp: ChildrenInput, ctx, session) -> Plan:
            return Plan(preview=children(noun, inp, session, principal_id=ctx.on_behalf_of))

        registry.command(f'{noun} query children', scope='company',
            description=f'Read a bounded page of an owned collection of {definition.singular_label.lower()}.',
            input_model=ChildrenInput, output_model=ChildrenOutput, required_role=show.required_role,
            capability=show.capability, error_codes=['E_LIST_FILTER', 'E_QUERY_STALE', 'E_RECORD_NOT_FOUND'])(child_page)


def _load_target(target: str | None = None) -> None:
    for noun in LIST_DEFINITIONS:
        if target is None or target == noun or target.startswith(noun + " ") or noun.startswith(target + " "):
            _register(noun)


_load_target(registry.loading_target())
