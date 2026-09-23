"""Finite authoritative domain JSON. Arbitrary rules and implicit imports are excluded."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(DomainModel):
    reference: str = Field(min_length=1, max_length=2000)
    owner: str = Field(min_length=1, max_length=160)
    document_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    section: str | None = None


class Concept(DomainModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*:[a-zA-Z][a-zA-Z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=160)
    kind: Literal["object", "result"]
    parents: list[str] = Field(default_factory=list, max_length=8)


class DomainRelation(DomainModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*:[a-zA-Z][a-zA-Z0-9_.-]*$")
    source_type: str
    target_type: str


class Unit(DomainModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    dimension: str = Field(min_length=1, max_length=64)


class Conversion(DomainModel):
    from_unit: str
    to_unit: str
    factor: float = Field(gt=0, allow_inf_nan=False)


class DomainDefinition(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    source: Source
    concepts: list[Concept] = Field(min_length=1, max_length=200)
    relations: list[DomainRelation] = Field(default_factory=list, max_length=200)
    units: list[Unit] = Field(default_factory=list, max_length=100)
    conversions: list[Conversion] = Field(default_factory=list, max_length=100)


class DomainCreate(DomainModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    definition: DomainDefinition


class DomainEdit(DomainModel):
    revision: int = Field(ge=1)
    definition: DomainDefinition


class DomainRef(DomainModel):
    package_id: str
    version: int = Field(ge=1)


class ParticipantRole(DomainModel):
    role: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    type: str
    required: bool = True
    min_count: int = Field(default=1, ge=0, le=8)
    max_count: int = Field(default=1, ge=1, le=8)

    @model_validator(mode="after")
    def cardinality(self):
        if self.min_count > self.max_count or self.required != (self.min_count > 0):
            raise ValueError("Required flag and explicit role cardinality disagree")
        return self


class RoleRelation(DomainModel):
    name: str
    source: str
    target: str


class ArgumentBinding(DomainModel):
    role: str
    resolver_ref: str = Field(pattern=r"^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+$")


class ParameterSemantic(DomainModel):
    kind: Literal["quantity", "date", "datetime"]
    unit: str | None = None
    allow_relative: bool = False


class ResultBinding(DomainModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    entity_type: str
    collection_path: str
    id_field: str
    label_field: str
    resolver_ref: str


class HistoryRef(DomainModel):
    call_id: str
    binding: str
    index: int | None = Field(default=None, ge=0, le=99)


class Participant(DomainModel):
    role: str
    type: str
    mention: str | None = Field(default=None, min_length=1, max_length=500)
    history_ref: HistoryRef | None = None

    @model_validator(mode="after")
    def source(self):
        if self.mention and self.history_ref:
            raise ValueError("Participant has one explicit binding source")
        return self
