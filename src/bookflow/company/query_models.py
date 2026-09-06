"""Typed optional master-list projections and bounded discovery."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextCriterion(Strict):
    definition: str
    kind: Literal['text']
    operator: Literal['eq', 'ne', 'contains']
    value: str


class NumberCriterion(Strict):
    definition: str
    kind: Literal['number']
    operator: Literal['eq', 'ne', 'lt', 'lte', 'gt', 'gte']
    value: str

    @field_validator('value')
    @classmethod
    def exact(cls, value):
        from bookflow.core.exact import parse_custom_number_nano_units, format_custom_number_nano_units
        coefficient = parse_custom_number_nano_units(value)
        if format_custom_number_nano_units(coefficient) != value:
            raise ValueError('must be a canonical decimal string')
        return value


class DateCriterion(Strict):
    definition: str
    kind: Literal['date']
    operator: Literal['eq', 'ne', 'lt', 'lte', 'gt', 'gte']
    value: str

    @field_validator('value')
    @classmethod
    def iso(cls, value):
        from datetime import date
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('must be a canonical ISO date')
        return value


class BoolCriterion(Strict):
    definition: str
    kind: Literal['bool']
    operator: Literal['eq', 'ne']
    value: bool


class ChoiceCriterion(Strict):
    definition: str
    kind: Literal['choice']
    operator: Literal['eq', 'ne']
    value: str = Field(description='Stable choice ID belonging to this definition, never its label.')


class PresenceCriterion(Strict):
    definition: str
    kind: Literal['presence']
    operator: Literal['is_missing', 'is_present']


CustomCriterion = Annotated[TextCriterion | NumberCriterion | DateCriterion | BoolCriterion | ChoiceCriterion | PresenceCriterion,
                            Field(discriminator='kind')]


class Descriptor(Strict):
    key: str
    label: str
    kind: Literal['text', 'number', 'integer', 'date', 'bool', 'choice', 'money', 'reference', 'object', 'collection', 'scalar']
    nullable: bool = False
    read_only: bool = True
    reference_noun: str | None = None
    operators: list[str] = Field(default_factory=list)
    sortable: bool = False
    sort_key: str | None = None
    definition: str | None = None
    active: bool = True
    owner_target: str | None = None
    choices: list[str] = Field(default_factory=list, description='Finite built-in enum values; custom choices use paged discovery.')
    currency: str | None = Field(default=None, description='Currency for a monetary filter value.')
    scale: int | None = Field(default=None, ge=0, le=9, description='Decimal places for monetary filter input; legacy filter strings use integer minor units.')


class OptionsInput(Strict):
    kind: Literal['columns', 'filters', 'sorts', 'choices'] = 'columns'
    definition: str | None = None
    query: str | None = None
    include_inactive: bool = True
    keys: list[str] | None = Field(default=None, max_length=64)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode='after')
    def choice_target(self):
        if (self.kind == 'choices') != (self.definition is not None):
            raise ValueError('definition is required only for choices discovery')
        return self


class OptionsOutput(Strict):
    kind: Literal['columns', 'filters', 'sorts', 'choices']
    items: list[Descriptor]
    count: int = Field(ge=0, le=200)
    next_cursor: str | None
    default_columns: list[str]

    @model_validator(mode='after')
    def page_count(self):
        if self.count != len(self.items):
            raise ValueError('count must equal returned descriptor count')
        return self


class SelectedItem(Strict):
    id: str
    version: int
    label: str
    active: bool
    values: dict[str, JsonValue]
