"""Typed commands for the shared point-of-sale layout."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_serializer

Style = Literal['block', 'list', 'compact', 'photo']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ListInput(Strict):
    pass


class Versioned(Strict):
    expected_version: int = Field(ge=0, strict=True, description='Current layout revision from pos-box list, '
                                 'not a box or company-settings version; zero is the unsaved category seed.')


class CreateInput(Versioned):
    kind: Literal['group', 'item']
    parent: str | None = Field(None, description='Parent group ID; null is Home.')
    position: int | None = Field(None, ge=0, description='Zero-based sibling position; omitted appends.')
    item: str | None = Field(None, description='Sales item ID or full name; required for an item box.')
    label: str | None = Field(None, max_length=400, description='Required name for a group; optional item label.')
    colour: str | None = Field(None, pattern=r'^#[0-9a-fA-F]{6}$')
    picture_attachment: str | None = Field(None, description='Existing JPEG, PNG or WebP attachment for a group.')


class TargetInput(Versioned):
    pos_box: str = Field(min_length=1, description='Box ID from pos-box list.')


class UpdateInput(TargetInput):
    label: str | None = Field(None, max_length=400)
    colour: str | None = Field(None, pattern=r'^#[0-9a-fA-F]{6}$')
    picture_attachment: str | None = Field(None, description='Group image attachment; null clears it.')

    @model_serializer(mode='wrap')
    def supplied(self, handler):
        values = handler(self)
        for key in ('label', 'colour', 'picture_attachment'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class MoveInput(TargetInput):
    parent: str | None = Field(None, description='Destination group ID; null is Home.')
    position: int = Field(ge=0, description='Zero-based destination position after removing the moved box.')


class ViewInput(Versioned):
    pos_box: str | None = Field(None, description='Group ID; null is Home.')
    style: Style


class SeedInput(Versioned):
    mode: Literal['categories', 'empty'] = Field('categories', description='Starting layout; only before the first edit.')


class Picture(Strict):
    attachment_id: str
    sha256: str
    media_type: str
    size_bytes: int
    url: str


class Box(Strict):
    id: str
    parent: str | None
    position: int
    kind: Literal['group', 'item']
    item: str | None
    inactive: bool = Field(description="Item or its category is inactive; the till skips this box, the editor allows removal.")
    label: str | None
    colour: str | None
    picture: Picture | None
    view_style: Style
    version: int


class Layout(Strict):
    items: list[Box]
    home_view_style: Style
    version: int = Field(description='Use this layout revision as expected_version for every edit.')
    initialized: bool = Field(description='False is the computed category seed; true also covers a saved empty layout.')
