from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATED_MODEL_ID = re.compile(r".+-\d{8}$")
ENDPOINT_TAG = re.compile(r"^[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*){0,2}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderPolicy(StrictModel):
    order: list[str] = Field(default_factory=list)
    allow_fallbacks: bool = True
    require_parameters: bool = False
    quantizations: list[str] = Field(default_factory=list)
    max_price: dict[str, Decimal] | None = None

    def has_exact_endpoint(self) -> bool:
        return len(self.order) == 1 and bool(ENDPOINT_TAG.fullmatch(self.order[0]))


class RetryPolicy(StrictModel):
    attempts: int = Field(default=2, ge=0, le=5)
    backoff_seconds: float = Field(default=0.5, ge=0)
    on_schema_violation: Literal["retry", "fail"] = "retry"


class RoleModel(StrictModel):
    model_id: str
    requires_dated_model_id: bool = False
    prompt_version: str
    json_schema_ref: str
    structured_output_required: bool = True
    provider: ProviderPolicy
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    budget: dict[str, Decimal] = Field(default_factory=dict)


class LlmContract(StrictModel):
    llm_contract_version: str
    roles: dict[str, RoleModel]


class Bm25sFieldWeighting(StrictModel):
    body: float = Field(ge=0)


class Bm25sConfig(StrictModel):
    version: str
    analyzer: str
    tokenizer: str
    k1: float = Field(ge=0)
    b: float = Field(ge=0, le=1)
    document_unit: Literal["message"]
    field_weighting: Bm25sFieldWeighting
    tie_order: str
    score_normalization: str


class RetrievalConfig(StrictModel):
    top_k: int = Field(default=20, ge=1)
    bm25_dense_weights: tuple[float, float] = (0.5, 0.5)
    bm25s: Bm25sConfig


class EmbeddingsConfig(StrictModel):
    text_embedding_model: str
    visual_model: str


class ContextStrategies(StrictModel):
    reply_thread: bool
    temporal: bool
    same_sender: bool
    participant: bool
    semantic: bool


class ContextConfig(StrictModel):
    strategies: ContextStrategies
    context_window: int = Field(default=8, ge=1)
    token_budget: int = Field(default=4000, ge=1)


class ControllerConfig(StrictModel):
    clue_extraction_strategy: str
    jump_query_strategy: str
    max_rounds: int = Field(default=3, ge=1, le=3)
    max_jumps: int = Field(default=2, ge=0)


class DatasetConfig(StrictModel):
    dup_threshold: float = Field(ge=0, le=1)
    phash_threshold: int = Field(ge=0)
    split_salt: str
    review_salt: str


class ScopeConfig(StrictModel):
    default_deny: bool = True
    default_caller_id: str | None = None


class AppConfig(StrictModel):
    mode: Literal["dev", "release"] = "dev"
    comparable: bool = False
    retrieval: RetrievalConfig
    embeddings: EmbeddingsConfig
    context: ContextConfig
    controller: ControllerConfig
    dataset: DatasetConfig
    scope: ScopeConfig
    llm: LlmContract

    @model_validator(mode="after")
    def validate_release_contract(self) -> AppConfig:
        if self.mode != "release":
            return self
        if not self.comparable:
            raise ValueError("release mode requires comparable=true")
        for role_name, role in self.llm.roles.items():
            provider = role.provider
            if role.structured_output_required and not provider.require_parameters:
                raise ValueError(f"release role {role_name} requires provider.require_parameters=true")
            if provider.allow_fallbacks:
                raise ValueError(f"release role {role_name} requires allow_fallbacks=false")
            if not provider.has_exact_endpoint():
                raise ValueError(f"release role {role_name} requires one exact provider endpoint")
            if role.requires_dated_model_id and DATED_MODEL_ID.fullmatch(role.model_id) is None:
                raise ValueError(f"release role {role_name} requires a dated model id")
        return self
