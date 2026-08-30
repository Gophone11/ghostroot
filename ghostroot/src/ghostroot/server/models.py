from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    intent_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)
    report_timeout: int = Field(ge=5)


class TranslateRequest(BaseModel):
    text: str
    target_lang: Literal["zh-CN", "zh-TW"] = "zh-CN"

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class TranslateResponse(BaseModel):
    translated_text: str
    provider: str


class Fact(BaseModel):
    id: str
    description: str
    kind: str | None = None
    outcome: str | None = None
    goal_relevance: str | None = None
    next_policy: str | None = None
    tags: list[str] = Field(default_factory=list)
    atoms: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("kind", "outcome", "goal_relevance", "next_policy")
    @classmethod
    def validate_optional_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("tags must not contain empty strings")
            cleaned.append(text)
        return cleaned

    @field_validator("atoms")
    @classmethod
    def validate_atoms(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, atom in enumerate(value):
            if not isinstance(atom, dict):
                raise ValueError(f"atoms[{index}] must be an object")
            for key in ("subject", "predicate", "object"):
                field = atom.get(key)
                if not isinstance(field, str) or not field.strip():
                    raise ValueError(f"atoms[{index}].{key} must be a non-empty string")
            polarity = atom.get("polarity")
            if polarity is not None and polarity not in ("positive", "negative"):
                raise ValueError(f"atoms[{index}].polarity must be positive or negative")
        return value


class Intent(BaseModel):
    id: str
    from_: list[str] = Field(alias="from")
    to: str | None = None
    description: str
    kind: str | None = None
    stop_condition: str | None = None
    creator: str
    worker: str | None = None
    last_heartbeat_at: str | None = None
    created_at: str
    concluded_at: str | None = None

    model_config = {"populate_by_name": True}

    @field_validator("kind", "stop_condition")
    @classmethod
    def validate_optional_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str


class ProjectReason(BaseModel):
    worker: str
    trigger: str
    started_at: str
    last_heartbeat_at: str


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: Literal["active", "stopped", "completed"]
    bootstrap_enabled: bool
    created_at: str
    reason: ProjectReason | None = None


class ProjectSummary(ProjectMeta):
    fact_count: int
    intent_count: int
    working_intent_count: int
    unclaimed_intent_count: int
    hint_count: int


class ProjectDetail(BaseModel):
    project: ProjectMeta
    facts: list[Fact]
    intents: list[Intent]
    hints: list[Hint]


class CreateHintInline(BaseModel):
    content: str
    creator: str

    @field_validator("content", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateProjectRequest(BaseModel):
    title: str
    origin: str
    goal: str
    bootstrap_enabled: bool = True
    hints: list[CreateHintInline] | None = None

    @field_validator("title", "origin", "goal")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateHintRequest(BaseModel):
    content: str
    creator: str

    @field_validator("content", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateIntentRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    kind: str | None = None
    creator: str
    worker: str | None = None

    model_config = {"populate_by_name": True}

    @field_validator("description", "kind", "creator", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        return cleaned


class HeartbeatRequest(BaseModel):
    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReasonClaimRequest(BaseModel):
    worker: str
    trigger: str

    @field_validator("worker", "trigger")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ConcludeRequest(BaseModel):
    worker: str
    description: str
    kind: str | None = None
    outcome: str | None = None
    goal_relevance: str | None = None
    next_policy: str | None = None
    stop_condition: str | None = None
    tags: list[str] = Field(default_factory=list)
    atoms: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("worker", "description", "kind", "outcome", "goal_relevance", "next_policy", "stop_condition")
    @classmethod
    def validate_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return Fact(id="validation", description="validation", tags=value).tags

    @field_validator("atoms")
    @classmethod
    def validate_atoms(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return Fact(id="validation", description="validation", atoms=value).atoms


class CompleteRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    worker: str

    model_config = {"populate_by_name": True}

    @field_validator("description", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        return cleaned


class ConcludeResponse(BaseModel):
    fact: Fact
    intent: Intent


class UpdateProjectStatusRequest(BaseModel):
    status: Literal["active", "stopped"]


class UpdateProjectTitleRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenRequest(BaseModel):
    description: str
    creator: str

    @field_validator("description", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenResponse(BaseModel):
    project: ProjectMeta
    fact: Fact
    intent: Intent


class ToolEvent(BaseModel):
    event_id: str
    project_id: str
    task_type: str
    phase: str
    intent_id: str | None = None
    worker: str | None = None
    task_run_id: str | None = None
    tool: str
    command: str
    cwd: str | None = None
    source: str = "path-wrapper"
    occurred_at: str
    recorded_at: str


class ToolEventInput(BaseModel):
    event_id: str
    timestamp: str
    task_type: str
    phase: str
    intent_id: str | None = None
    worker: str | None = None
    task_run_id: str | None = None
    tool: str
    command: str
    cwd: str | None = None
    source: str = "path-wrapper"

    @field_validator("event_id", "timestamp", "task_type", "phase", "tool", "command", "source")
    @classmethod
    def validate_non_empty_tool_event_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("intent_id", "worker", "task_run_id", "cwd")
    @classmethod
    def normalize_optional_tool_event_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class RecordToolEventsRequest(BaseModel):
    events: list[ToolEventInput]


class ProjectMetrics(BaseModel):
    project_id: str
    fact_count: int
    intent_count: int
    tool_event_count: int
    action_step_count: int
    execution_episode_count: int
    tool_event_counts_by_task_type: dict[str, int]
    tool_event_counts_by_tool: dict[str, int]


ReportStatus = Literal["pending", "generating", "ready", "failed"]
ReportConfidence = Literal["high", "medium", "low"]


class ReportPathStep(BaseModel):
    title: str
    source_facts: list[str]
    intent_ids: list[str]
    result_fact: str | None = None
    why_it_matters: str

    @field_validator("title", "why_it_matters")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ProjectReport(BaseModel):
    id: str
    project_id: str
    status: ReportStatus
    markdown: str | None = None
    attack_path_summary: list[ReportPathStep] | None = None
    confidence: ReportConfidence | None = None
    gaps: list[str] | None = None
    error: str | None = None
    generator: str | None = None
    source_completed_intent_id: str | None = None
    created_at: str
    started_at: str | None = None
    last_heartbeat_at: str | None = None
    generated_at: str | None = None


class ReportClaimRequest(BaseModel):
    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReportCompleteRequest(BaseModel):
    worker: str
    markdown: str
    attack_path_summary: list[ReportPathStep]
    confidence: ReportConfidence
    gaps: list[str] = []

    @field_validator("worker", "markdown")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReportFailRequest(BaseModel):
    worker: str
    error: str

    @field_validator("worker", "error")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReportContext(BaseModel):
    project: ProjectMeta
    facts: list[Fact]
    intents: list[Intent]
    hints: list[Hint]
    main_path_intent_ids: list[str]
    timeline: str
