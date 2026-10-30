from pydantic import BaseModel, ConfigDict, Field


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    topic: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=100)
    platform: str = Field(min_length=1, max_length=100)
    target_seconds: float = Field(gt=0, le=600, allow_inf_nan=False)
    style: str = Field(min_length=1, max_length=100)
    duration_tolerance: float = Field(default=0.2, ge=0, lt=1, allow_inf_nan=False)
    initial_script: dict | None = None


class Fact(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    visual: str = Field(min_length=1)
    aspects: list[str] = Field(default_factory=lambda: ["feature"])


class Material(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    topics: list[str]
    search_terms: list[str]
    facts: list[Fact]
    topic_patterns: list[list[list[str]]] = Field(default_factory=list)
    topic_concept: str = ""


class Rules(BaseModel):
    allowed_generic_titles: list[str]
    forbidden_terms: list[str]
    narration_characters_per_second: float = Field(gt=0, allow_inf_nan=False)
    minimum_scene_seconds: float = Field(gt=0, allow_inf_nan=False)
    topic_aspects: dict[str, list[str]] = Field(default_factory=dict)
    topic_filler: list[str] = Field(default_factory=list)
    aspect_evidence_patterns: dict[str, str] = Field(default_factory=dict)


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    fact_id: str
    narration: str = Field(min_length=1)
    subtitle: str = Field(min_length=1)
    visual: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0, le=600, allow_inf_nan=False)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1, max_length=12)
