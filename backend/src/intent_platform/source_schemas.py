from datetime import datetime
from typing import Any, Literal
from pydantic import Field
from .schemas import StrictModel


class SectionOut(StrictModel):
    id: str
    heading: str
    start_line: int
    end_line: int
    text: str


class DocumentOut(StrictModel):
    id: str
    filename: str
    revision: int
    text: str
    sections: list[SectionOut]
    original_available: bool
    source: dict
    fixture_mode: bool = False


class DocumentSummary(StrictModel):
    id: str
    filename: str
    latest_revision: int


class ImportRequest(StrictModel):
    document_id: str
    revision: int = Field(ge=1)


class IngestAccepted(StrictModel):
    job_id: str
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]


class IngestJobOut(IngestAccepted):
    kind: Literal["document.create", "document.revise", "openapi.parse"]
    created_at: datetime
    updated_at: datetime
    result: dict | None = None
    error: dict | None = None


class Diagnostic(StrictModel):
    code: str
    message: str
    pointer: str
    severity: Literal["error", "review"] = "error"


class OperationOut(StrictModel):
    key: str
    method: str
    path: str
    operation_id: str | None
    summary: str
    source_pointer: str
    supported: bool
    diagnostics: list[Diagnostic]
    definition: dict | None
    facts: dict = Field(default_factory=dict)


class ImportOut(StrictModel):
    id: str
    document_id: str
    document_revision: int
    parser_version: str
    valid: bool
    diagnostics: list[Diagnostic]
    operations: list[OperationOut]


class OperationSelection(StrictModel):
    operation_key: str
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=12000)


class Citation(StrictModel):
    field_path: str
    document_id: str
    revision: int = Field(ge=1)
    section_id: str
    quote: str = Field(min_length=1, max_length=6000)
    proposed_value: Any


class SourcesBody(StrictModel):
    revision: int = Field(ge=1)
    citations: list[Citation] = Field(max_length=100)


class Resolution(StrictModel):
    field_path: str
    reason: str = Field(min_length=3, max_length=2000)


class ReviewBody(StrictModel):
    revision: int = Field(ge=1)
    resolutions: list[Resolution] = Field(min_length=1, max_length=100)


class ReviewIssue(StrictModel):
    field_path: str
    kind: Literal["conflict", "review"]
    current_value: Any
    candidates: list[dict]
    resolved: bool
    reason: str | None


class SourceReviewOut(StrictModel):
    revision: int
    required: bool
    ready: bool
    issues: list[ReviewIssue]
    api_source: dict | None = None
    api_facts: dict = Field(default_factory=dict)
    citations: list[dict] = Field(default_factory=list)
