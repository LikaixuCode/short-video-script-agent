from pydantic import BaseModel, ConfigDict, Field


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    topic: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=100)
    platform: str = Field(min_length=1, max_length=100)
    target_seconds: float = Field(gt=0, le=600, allow_inf_nan=False)
    style: str = Field(min_length=1, max_length=100)
    initial_script: dict | None = None


class Fact(BaseModel):
    id: str
    text: str
    visual: str


class Material(BaseModel):
    id: str
    title: str
    topics: list[str]
    search_terms: list[str]
    facts: list[Fact]


class Rules(BaseModel):
    allowed_generic_titles: list[str]
    forbidden_terms: list[str]
    narration_characters_per_second: float = Field(gt=0, allow_inf_nan=False)
    minimum_scene_seconds: float = Field(gt=0, allow_inf_nan=False)


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
