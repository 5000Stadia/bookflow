"""Relationship setup inputs. All counterpart references require both-side admission."""
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Peer(Strict):
    peer: str = Field(min_length=26, max_length=26, description='Registered counterpart company ID; names are not selectors.')


class MappingSet(Peer):
    due_from: str = Field(min_length=26, max_length=26, description='Local unused active other-current-asset account ID.')
    due_to: str = Field(min_length=26, max_length=26, description='Local unused active other-current-liability account ID.')
    expected_version: int = Field(ge=0, strict=True, description='Latest local mapping version; zero for first setup.')


class Mapping(Strict):
    id: str
    version: int
    due_from_id: str
    due_to_id: str
    currency: str
    digest: str


class RelationshipSelect(Peer):
    relationship: str = Field(min_length=26, max_length=26)


class RelationshipUpdate(RelationshipSelect):
    expected_version: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True, description='Enable or disable new use; existing history survives.')


class Relationship(Strict):
    id: str
    version: int
    peer_id: str
    peer_name: str
    enabled: bool
    local_mapping_digest: str
    peer_mapping_digest: str


class RelationshipList(Strict):
    limit: int = Field(50, ge=1, le=200)
    cursor: str | None = Field(None, max_length=26, description='Last authorized relationship ID from preceding page.')


class RelationshipPage(Strict):
    items: list[Relationship]
    next_cursor: str | None = None
